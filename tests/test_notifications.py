"""Benachrichtigungen (issue #258): signierter Webhook, E-Mail über SMTP, Trigger, Rate-Limit und Audit-Log."""

import hashlib
import hmac
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from chronicle import accounts, notifications, security
from chronicle.models import EmailConfig, Notifications, WebhookConfig

PASSWORD = "correct horse battery"
TOPIC = "inc-2026-042"
SECRET = "test-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def notify_env(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")
    monkeypatch.setenv("CHRONICLE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("CHRONICLE_NOTIFY_COOLDOWN_SECONDS", "0")
    notifications.reset_state()
    FakeSMTP.instances.clear()
    yield
    notifications.reset_state()


@pytest.fixture
def make_client(tmp_path, folder):
    """Like conftest.api_client, but every call gets its own user, so one test can hold several roles."""
    from fastapi.testclient import TestClient

    from chronicle.api import create_app
    from chronicle.db import connect

    names = itertools.count()

    def make(role="analyst"):
        name = f"user{next(names)}"
        conn = connect(tmp_path / "chronicle.db")
        accounts.create_user(conn, name, security.hash_password(PASSWORD), role)
        conn.close()
        client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
        response = client.post("/api/v1/auth/login", json={"username": name, "password": PASSWORD})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    return make


@pytest.fixture
def webhook_server():
    """A local webhook receiver; yields its URL and the list of received requests."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append({"headers": dict(self.headers), "body": self.rfile.read(length)})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/hook", received
    server.shutdown()
    thread.join(timeout=5)


def set_notifications(client, key, notes):
    topic = client.get(f"/api/v1/topics/{key}").json()
    body = {name: value for name, value in topic.items() if name != "updated_at"}
    body["notifications"] = notes
    response = client.put(f"/api/v1/topics/{key}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def create_event(client, key, **extra):
    body = {
        "key": key,
        "t0": "2026-10-15T09:00:00+02:00",
        "phase": "Analysis",
        "actor": "anna",
        "action": f"Aktion {key}",
        "status": "open",
        "topic": TOPIC,
        "topics": [TOPIC],
        **extra,
    }
    response = client.post("/api/v1/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def set_status(client, key, status):
    event = client.get(f"/api/v1/events/{key}").json()
    body = {
        name: event[name]
        for name in (
            "key",
            "t0",
            "phase",
            "actor",
            "system",
            "action",
            "status",
            "triggered_by",
            "techniques",
            "tags",
            "detection",
        )
    }
    body.update(
        {
            "status": status,
            "details": event.get("details") or "",
            "topic": event["topics"][0],
            "topics": event["topics"],
            "version": event["version"],
        }
    )
    response = client.put(f"/api/v1/events/{key}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def audit_rows(folder, action_prefix="notification"):
    from chronicle.db import connect

    with connect(folder.parent / "chronicle.db") as conn:
        return [
            dict(row)
            for row in conn.execute(
                f"SELECT action, target, detail FROM ops.audit_log WHERE action LIKE '{action_prefix}%' ORDER BY pk"
            )
        ]


# --- the model -----------------------------------------------------------------------


def test_webhook_url_requires_https_except_loopback():
    with pytest.raises(ValidationError):
        WebhookConfig(url="http://hooks.example.com/x")
    with pytest.raises(ValidationError):
        WebhookConfig(url="https://user:pw@hooks.example.com/x")
    assert WebhookConfig(url="https://hooks.example.com/x").url == "https://hooks.example.com/x"
    assert WebhookConfig(url="http://127.0.0.1:9000/hook").url == "http://127.0.0.1:9000/hook"
    assert WebhookConfig(url="http://localhost:9000/hook").url == "http://localhost:9000/hook"


def test_email_recipients_are_validated():
    with pytest.raises(ValidationError):
        EmailConfig(to=["not-an-email"])
    with pytest.raises(ValidationError):
        EmailConfig(to=[])
    assert EmailConfig(to=["soc@example.com"]).to == ["soc@example.com"]


def test_notifications_needs_at_least_one_channel():
    with pytest.raises(ValidationError):
        Notifications()


# --- webhook delivery ------------------------------------------------------------------


def test_webhook_receives_signed_payload_for_a_new_event(make_client, folder, webhook_server):
    url, received = webhook_server
    admin = make_client("admin")
    set_notifications(admin, TOPIC, {"webhook": {"url": url, "triggers": ["new_event"]}})
    create_event(admin, "ev-notify-1")
    notifications.flush()

    assert len(received) == 1
    request = received[0]
    payload = json.loads(request["body"])
    assert payload["type"] == "chronicle.notification"
    assert payload["trigger"] == "new_event"
    assert payload["topic"]["key"] == TOPIC
    assert payload["event"]["key"] == "ev-notify-1"
    assert payload["link"] == "/events/ev-notify-1"
    # HMAC-SHA256 over the raw body
    expected = "sha256=" + hmac.new(SECRET.encode(), request["body"], hashlib.sha256).hexdigest()
    assert request["headers"].get("X-Chronicle-Signature") == expected
    assert request["headers"].get("X-Chronicle-Trigger") == "new_event"
    assert request["headers"].get("Content-Type") == "application/json"

    rows = audit_rows(folder)
    assert len(rows) == 1
    assert rows[0]["action"] == "notification_sent"
    assert rows[0]["target"] == TOPIC
    assert "webhook new_event http=200" == rows[0]["detail"]
    assert "ev-notify-1" not in rows[0]["detail"]  # no content in the audit log


def test_status_change_and_rule_match_triggers(make_client, folder, webhook_server):
    url, received = webhook_server
    admin = make_client("admin")
    set_notifications(admin, TOPIC, {"webhook": {"url": url, "triggers": ["status_change", "rule_match"]}})

    create_event(admin, "ev-plain")  # no detection: no rule_match
    set_status(admin, "ev-plain", "resolved")
    create_event(admin, "ev-detected", detection={"rule_id": "SIGMA-1", "source": "Elastic"})
    notifications.flush()

    triggers = [json.loads(item["body"])["trigger"] for item in received]
    assert triggers == ["status_change", "rule_match"]
    change = json.loads(received[0]["body"])
    assert change["status_change"] == {"from": "open", "to": "resolved"}
    match = json.loads(received[1]["body"])
    assert match["event"]["detection"]["rule_id"] == "SIGMA-1"


def test_new_evidence_triggers(make_client, folder, webhook_server):
    url, received = webhook_server
    admin = make_client("admin")
    set_notifications(admin, TOPIC, {"webhook": {"url": url, "triggers": ["new_evidence"]}})
    create_event(admin, "ev-evidence")
    response = admin.post(
        "/api/v1/events/ev-evidence/evidence",
        files={"file": ("log.txt", b"zeile1\n", "text/plain")},
        data={"type": "log", "description": ""},
    )
    assert response.status_code == 201, response.text
    notifications.flush()

    assert len(received) == 1
    payload = json.loads(received[0]["body"])
    assert payload["trigger"] == "new_evidence"
    assert payload["evidence"]["file"].endswith("log.txt")
    assert payload["evidence"]["type"] == "log"


def test_no_notification_without_config(make_client, folder, webhook_server):
    url, received = webhook_server
    admin = make_client("admin")
    create_event(admin, "ev-quiet")
    notifications.flush()
    assert received == []
    assert audit_rows(folder) == []


def test_webhook_not_sent_without_secret(make_client, folder, webhook_server, monkeypatch):
    url, received = webhook_server
    monkeypatch.delenv("CHRONICLE_WEBHOOK_SECRET")
    admin = make_client("admin")
    set_notifications(admin, TOPIC, {"webhook": {"url": url, "triggers": ["new_event"]}})
    create_event(admin, "ev-unsigned")
    notifications.flush()
    assert received == []
    rows = audit_rows(folder)
    assert rows[0]["action"] == "notification_failed"
    assert "CHRONICLE_WEBHOOK_SECRET" in rows[0]["detail"]


def test_cooldown_suppresses_a_flood(make_client, folder, webhook_server, monkeypatch):
    url, received = webhook_server
    monkeypatch.setenv("CHRONICLE_NOTIFY_COOLDOWN_SECONDS", "60")
    admin = make_client("admin")
    set_notifications(admin, TOPIC, {"webhook": {"url": url, "triggers": ["new_event"]}})
    create_event(admin, "ev-flood-1")
    create_event(admin, "ev-flood-2")
    create_event(admin, "ev-flood-3")
    notifications.flush()
    assert len(received) == 1  # only the first one goes out
    rows = audit_rows(folder)
    assert rows[0]["action"] == "notification_sent"
    suppressed = [row for row in rows if row["action"] == "notification_suppressed"]
    assert len(suppressed) == 2
    assert suppressed[0]["detail"] == "webhook new_event rate_limit"


def test_webhook_retries_and_reports_failure(make_client, folder, monkeypatch):
    attempts = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            attempts.append(1)
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/hook"
    monkeypatch.setattr(notifications, "WEBHOOK_ATTEMPTS", 2)
    monkeypatch.setattr(notifications.time, "sleep", lambda *_: None)  # no waiting in the test

    admin = make_client("admin")
    set_notifications(admin, TOPIC, {"webhook": {"url": url, "triggers": ["new_event"]}})
    create_event(admin, "ev-retry")
    notifications.flush()
    server.shutdown()

    assert len(attempts) == 2  # 1 try + 1 retry on 5xx
    rows = audit_rows(folder)
    assert rows[0]["action"] == "notification_failed"
    assert "http=500" in rows[0]["detail"]


# --- e-mail ----------------------------------------------------------------------------


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.tls = False
        self.login_args = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, message):
        self.sent.append(message)

    def quit(self):
        pass


def test_email_is_sent_via_smtp(make_client, folder, monkeypatch):
    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("CHRONICLE_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("CHRONICLE_SMTP_USER", "chronicle")
    monkeypatch.setenv("CHRONICLE_SMTP_PASSWORD", "pw")
    admin = make_client("admin")
    set_notifications(admin, TOPIC, {"email": {"to": ["soc@example.com"], "triggers": ["new_event"]}})
    create_event(admin, "ev-mail")
    notifications.flush()

    assert len(FakeSMTP.instances) == 1
    server = FakeSMTP.instances[0]
    assert server.host == "smtp.example.com" and server.port == 587 and server.tls
    assert server.login_args == ("chronicle", "pw")
    assert len(server.sent) == 1
    message = server.sent[0]
    assert message["To"] == "soc@example.com"
    assert TOPIC in message["Subject"] and "ev-mail" in message["Subject"]
    assert "Aktion ev-mail" in message.get_content()

    rows = audit_rows(folder)
    assert rows[0]["action"] == "notification_sent"
    assert rows[0]["detail"] == "email new_event smtp ok"


def test_email_disabled_without_smtp_host(make_client, folder, monkeypatch):
    monkeypatch.delenv("CHRONICLE_SMTP_HOST", raising=False)
    admin = make_client("admin")
    set_notifications(admin, TOPIC, {"email": {"to": ["soc@example.com"], "triggers": ["new_event"]}})
    create_event(admin, "ev-nomail")
    notifications.flush()
    assert FakeSMTP.instances == []
    rows = audit_rows(folder)
    assert rows[0]["action"] == "notification_failed"
    assert "CHRONICLE_SMTP_HOST" in rows[0]["detail"]


# --- admin UI: configure, test, roles ----------------------------------------------------


def csrf_of(client):
    return {"csrf_token": client.headers["X-CSRF-Token"]}


def post(client, path, **data):
    return client.post(path, data={**csrf_of(client), **data}, follow_redirects=False)


def test_save_notifications_is_admin_only_and_persisted(make_client, folder, webhook_server):
    url, _ = webhook_server
    analyst = make_client("analyst")
    response = post(
        analyst, f"/topics/{TOPIC}/notifications", webhook_url=url, webhook_triggers=["new_event"], email_to=""
    )
    assert response.status_code == 403

    admin = make_client("admin")
    response = post(
        admin,
        f"/topics/{TOPIC}/notifications",
        webhook_url=url,
        webhook_triggers=["new_event", "status_change"],
        email_to="soc@example.com",
        email_triggers=["new_event"],
    )
    assert response.status_code == 303, response.text

    topic = admin.get(f"/api/v1/topics/{TOPIC}").json()
    assert topic["notifications"]["webhook"]["url"] == url
    assert topic["notifications"]["webhook"]["triggers"] == ["new_event", "status_change"]
    assert topic["notifications"]["email"]["to"] == ["soc@example.com"]
    # the config is in the topic file (and so in Git), without any secret
    text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert "notifications:" in text and url in text and SECRET not in text

    # the regular edit form of an analyst keeps the notification config
    response = post(
        analyst, f"/topics/{TOPIC}/edit", title="Umbenannt", kind="incident", summary="", rep_regulation="none"
    )
    assert response.status_code == 303, response.text
    assert "notifications:" in (folder / "timeline.yaml").read_text(encoding="utf-8")


def test_notifications_section_is_rendered_for_admins_only(make_client, folder):
    admin = make_client("admin")
    page = admin.get(f"/topics/{TOPIC}")
    assert page.status_code == 200
    assert "Benachrichtigungen" in page.text
    assert "Test-Benachrichtigung senden" in page.text

    analyst = make_client("analyst")
    assert "Benachrichtigungen" not in analyst.get(f"/topics/{TOPIC}").text

    viewer = make_client("viewer")
    assert "Benachrichtigungen" not in viewer.get(f"/topics/{TOPIC}").text


def test_test_notification_button_sends_and_logs(make_client, folder, webhook_server):
    url, received = webhook_server
    admin = make_client("admin")
    set_notifications(admin, TOPIC, {"webhook": {"url": url, "triggers": ["new_event"]}})

    viewer = make_client("viewer")
    assert post(viewer, f"/topics/{TOPIC}/notifications/test").status_code == 403
    analyst = make_client("analyst")
    assert post(analyst, f"/topics/{TOPIC}/notifications/test").status_code == 403

    response = post(admin, f"/topics/{TOPIC}/notifications/test")
    assert response.status_code == 303
    assert response.headers["location"] == f"/topics/{TOPIC}?notify=sent"
    assert len(received) == 1
    payload = json.loads(received[0]["body"])
    assert payload["trigger"] == "test" and "event" not in payload
    rows = audit_rows(folder)
    assert rows[0]["action"] == "notification_sent" and rows[0]["detail"].startswith("webhook test ")

    # the flash message shows on the topic page
    page = admin.get(f"/topics/{TOPIC}?notify=sent")
    assert "Test-Benachrichtigung wurde versendet" in page.text


def test_test_notification_without_config_reports_disabled(make_client, folder):
    admin = make_client("admin")
    response = post(admin, f"/topics/{TOPIC}/notifications/test")
    assert response.headers["location"] == f"/topics/{TOPIC}?notify=disabled"
    assert audit_rows(folder) == []


def test_invalid_webhook_url_is_rejected_in_the_form(make_client, folder):
    admin = make_client("admin")
    response = post(
        admin,
        f"/topics/{TOPIC}/notifications",
        webhook_url="http://evil.example.com/x",
        webhook_triggers=["new_event"],
        email_to="",
    )
    assert response.status_code == 422
    assert "https" in response.text
