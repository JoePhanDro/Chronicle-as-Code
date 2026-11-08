"""Benachrichtigungen bei neuem Event, Statusänderung, neuem Evidence und Regel-Treffer (issue #258).

Konfiguration pro Topic in der YAML-Datei (topic.notifications: Webhook-URL und E-Mail-Empfänger, je Kanal die
Trigger). Die Secrets liegen in der Umgebung und landen nie in den Dateien, die in Git liegen:
CHRONICLE_WEBHOOK_SECRET (HMAC-SHA256-Signatur des Webhook-Bodies) und CHRONICLE_SMTP_* (E-Mail-Versand).
Ohne Secret wird der Kanal nicht beliefert — ein un-signierter Webhook wäre fälschbar.

Der Versand läuft in einem Daemon-Thread, damit ein lahmer Empfänger die Anfrage nicht aufhält. Jede Zustellung,
der Fehler und jedes wegen Rate-Limit unterdrückte Versenden landet ohne Inhalt im Audit-Log.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from datetime import UTC, datetime
from email.message import EmailMessage

from . import __version__, accounts
from .db import connect

TRIGGERS = ("new_event", "status_change", "new_evidence", "rule_match")
TRIGGER_LABELS = {
    "new_event": "Neues Event",
    "status_change": "Statusänderung",
    "new_evidence": "Neues Evidence",
    "rule_match": "Regel-Treffer",
    "test": "Test-Benachrichtigung",
}

WEBHOOK_TIMEOUT = 10.0
WEBHOOK_ATTEMPTS = 3  # 1 try + 2 retries on timeouts and 5xx; 4xx fails at once
SMTP_TIMEOUT = 10.0
COOLDOWN_DEFAULT = 60.0  # seconds between two notifications of the same topic+channel+trigger

_EVENT_FIELDS = ("key", "t0", "phase", "actor", "system", "action", "status", "techniques", "tags", "detection")

_queue: queue.Queue = queue.Queue()
_worker_lock = threading.Lock()
_worker: threading.Thread | None = None
_cooldown_lock = threading.Lock()
_last_sent: dict[tuple[str, str, str], float] = {}


class DeliveryError(Exception):
    """The webhook could not be delivered; the message is a short reason without payload content."""


# --- configuration from the environment ---------------------------------------------


def webhook_secret() -> str | None:
    """The HMAC secret for webhook signatures; without it no webhook is sent."""
    return os.environ.get("CHRONICLE_WEBHOOK_SECRET", "").strip() or None


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def smtp_settings() -> dict | None:
    """The SMTP configuration, or None when no host is set (e-mail channel disabled)."""
    host = os.environ.get("CHRONICLE_SMTP_HOST", "").strip()
    if not host:
        return None
    try:
        port = int(os.environ.get("CHRONICLE_SMTP_PORT", "") or 587)
    except ValueError:
        port = 587
    return {
        "host": host,
        "port": port,
        "ssl": _env_flag("CHRONICLE_SMTP_SSL", False),
        "starttls": _env_flag("CHRONICLE_SMTP_STARTTLS", True),
        "user": os.environ.get("CHRONICLE_SMTP_USER", "").strip() or None,
        "password": os.environ.get("CHRONICLE_SMTP_PASSWORD", "") or None,
        "from": os.environ.get("CHRONICLE_SMTP_FROM", "").strip() or f"chronicle@{host}",
        "timeout": _env_float("CHRONICLE_SMTP_TIMEOUT", SMTP_TIMEOUT),
    }


def cooldown_seconds() -> float:
    """Seconds between two notifications of the same topic+channel+trigger (rate limit, issue #258). 0 disables."""
    return max(0.0, _env_float("CHRONICLE_NOTIFY_COOLDOWN_SECONDS", COOLDOWN_DEFAULT))


def base_url() -> str | None:
    """Public base URL for links in e-mails (CHRONICLE_BASE_URL), e.g. https://chronicle.example.com."""
    return os.environ.get("CHRONICLE_BASE_URL", "").strip().rstrip("/") or None


def status() -> dict:
    """For the admin UI: which channels could deliver at all, without revealing any secret."""
    return {"webhook_secret": webhook_secret() is not None, "smtp": smtp_settings() is not None}


# --- payload ------------------------------------------------------------------------


def _payload(trigger: str, topic: dict, event: dict | None, extra: dict | None = None) -> dict:
    payload = {
        "type": "chronicle.notification",
        "trigger": trigger,
        "sent_at": datetime.now(UTC).isoformat(),
        "topic": {"key": topic["key"], "title": topic["title"]},
    }
    if event is not None:
        payload["event"] = {name: event[name] for name in _EVENT_FIELDS if event.get(name) is not None}
        payload["link"] = f"/events/{event['key']}"
    else:
        payload["link"] = f"/topics/{topic['key']}"
    if extra:
        payload.update(extra)
    return payload


# --- sending ------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are not followed: a webhook must not be bounced to another host (SSRF)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _send_webhook(url: str, payload: dict, secret: str, timeout: float = WEBHOOK_TIMEOUT) -> str:
    """POST the payload, signed with HMAC-SHA256. Returns an outcome like "http=200"; raises DeliveryError otherwise."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Chronicle-Signature": f"sha256={signature}",
            "X-Chronicle-Trigger": str(payload.get("trigger", "")),
            "X-Chronicle-Delivery": uuid.uuid4().hex,
            "User-Agent": f"Chronicle/{__version__}",
        },
    )
    reason = "unknown"
    for attempt in range(1, WEBHOOK_ATTEMPTS + 1):
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                if 200 <= response.status < 300:
                    return f"http={response.status}"
                reason = f"http={response.status}"
        except urllib.error.HTTPError as exc:
            reason = f"http={exc.code}"
            if exc.code < 500:
                break  # a 4xx will not get better by retrying
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = type(exc).__name__
        if attempt < WEBHOOK_ATTEMPTS:
            time.sleep(attempt)  # 1 s, then 2 s
    raise DeliveryError(reason)


def _send_email(settings: dict, to: list[str], subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["from"]
    message["To"] = ", ".join(to)
    message.set_content(body)
    if settings["ssl"]:
        server = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=settings["timeout"])
    else:
        server = smtplib.SMTP(settings["host"], settings["port"], timeout=settings["timeout"])
    try:
        if not settings["ssl"] and settings["starttls"]:
            server.starttls(context=ssl.create_default_context())
        if settings["user"]:
            server.login(settings["user"], settings["password"] or "")
        server.send_message(message)
    finally:
        try:
            server.quit()
        except OSError:
            pass


def _email_text(trigger: str, topic: dict, event: dict | None, extra: dict | None) -> tuple[str, str]:
    """Subject and plain-text body of the e-mail (German, like the reports). No evidence content, only names."""
    label = TRIGGER_LABELS.get(trigger, trigger)
    if event is not None:
        subject = f"[Chronicle] {label}: {topic['key']} – {str(event.get('action', ''))[:80]}"
    else:
        subject = f"[Chronicle] {label}: {topic['key']}"
    lines = [f"{label} im Topic „{topic['title']}“ ({topic['key']})", ""]
    if event is not None:
        lines.append(f"Event: {event.get('action', '')} ({event.get('key', '')})")
        lines.append(
            f"Zeit (UTC): {event.get('t0', '')} · Phase: {event.get('phase', '')} · Status: {event.get('status', '')}"
        )
        line = f"Akteur: {event.get('actor', '')}"
        if event.get("system"):
            line += f" · System: {event['system']}"
        lines.append(line)
        if extra and extra.get("status_change"):
            change = extra["status_change"]
            lines.append(f"Status: {change.get('from', '')} → {change.get('to', '')}")
        if extra and extra.get("evidence"):
            lines.append(f"Evidence: {extra['evidence'].get('file', '')} ({extra['evidence'].get('type', '')})")
        base = base_url()
        lines += ["", f"Link: {f'{base}/events/{event['key']}' if base else f'/events/{event['key']}'}"]
    else:
        lines.append("Test-Benachrichtigung ohne Event.")
        base = base_url()
        lines.append(f"Link: {f'{base}/topics/{topic['key']}' if base else f'/topics/{topic['key']}'}")
    return subject, "\n".join(lines)


def _deliver_one(
    conn,
    topic: dict,
    trigger: str,
    event: dict | None,
    extra: dict | None,
    channel: dict,
    actor_id: int | None,
    ip: str | None,
) -> str:
    """Deliver one notification on one channel and audit the outcome (without content). Returns the outcome."""
    kind = channel["channel"]
    payload = _payload(trigger, topic, event, extra)
    if kind == "webhook":
        secret = webhook_secret()
        if not secret:
            outcome = "disabled: CHRONICLE_WEBHOOK_SECRET fehlt"
        else:
            try:
                outcome = _send_webhook(channel["url"], payload, secret)
            except DeliveryError as exc:
                outcome = f"failed: {exc}"
    else:
        settings = smtp_settings()
        if not settings:
            outcome = "disabled: CHRONICLE_SMTP_HOST fehlt"
        else:
            subject, body = _email_text(trigger, topic, event, extra)
            try:
                _send_email(settings, channel["to"], subject, body)
                outcome = "smtp ok"
            except (OSError, smtplib.SMTPException) as exc:
                outcome = f"failed: {type(exc).__name__}"
    action = "notification_sent" if not outcome.startswith(("failed", "disabled")) else "notification_failed"
    accounts.audit(conn, action, user_id=actor_id, target=topic["key"], detail=f"{kind} {trigger} {outcome}", ip=ip)
    return outcome


# --- queue and worker ----------------------------------------------------------------


def _db_path_of(conn) -> str:
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            return row[2]
    raise RuntimeError("kein Haupt-Datenbankfile an der Verbindung")


def _topic_row(conn, key: str) -> dict | None:
    row = conn.execute("SELECT title, notifications FROM topics WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return {
        "key": key,
        "title": row["title"],
        "notifications": json.loads(row["notifications"]) if row["notifications"] else None,
    }


def _claim(topic_key: str, channel: str, trigger: str) -> bool:
    """Rate limit: one notification per topic+channel+trigger within the cooldown, so an import flood
    does not spam (issue #258). True when this one may go out."""
    cooldown = cooldown_seconds()
    if cooldown <= 0:
        return True
    now = time.monotonic()
    with _cooldown_lock:
        if len(_last_sent) > 10000:
            _last_sent.clear()
        last = _last_sent.get((topic_key, channel, trigger))
        if last is not None and now - last < cooldown:
            return False
        _last_sent[(topic_key, channel, trigger)] = now
        return True


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="chronicle-notifications", daemon=True)
            _worker.start()


def enqueue(
    conn,
    trigger: str,
    event: dict | None,
    topic_keys: list[str],
    extra: dict | None = None,
    actor_id: int | None = None,
    ip: str | None = None,
) -> None:
    """Queue the notifications for one trigger: every topic of the event whose config has this trigger on a
    channel. `extra` carries additional payload fields (status change, evidence name). Called after the write
    succeeded, so a failed delivery never blocks or rolls back the change."""
    db_path = _db_path_of(conn)
    for key in topic_keys:
        topic = _topic_row(conn, key)
        if not topic or not topic["notifications"]:
            continue
        cfg = topic["notifications"]
        channels = []
        for kind, channel_cfg in (("webhook", cfg.get("webhook")), ("email", cfg.get("email"))):
            if not channel_cfg or trigger not in (channel_cfg.get("triggers") or []):
                continue
            if not _claim(key, kind, trigger):
                accounts.audit(
                    conn,
                    "notification_suppressed",
                    user_id=actor_id,
                    target=key,
                    detail=f"{kind} {trigger} rate_limit",
                    ip=ip,
                )
                continue
            channels.append({"channel": kind, "url": channel_cfg.get("url"), "to": channel_cfg.get("to")})
        if channels:
            _ensure_worker()
            _queue.put(
                {
                    "db_path": db_path,
                    "topic": {"key": key, "title": topic["title"]},
                    "trigger": trigger,
                    "event": event,
                    "extra": extra,
                    "channels": channels,
                    "actor_id": actor_id,
                    "ip": ip,
                }
            )


def _work() -> None:
    while True:
        item = _queue.get()
        try:
            with closing(connect(item["db_path"])) as conn:
                for channel in item["channels"]:
                    try:
                        _deliver_one(
                            conn,
                            item["topic"],
                            item["trigger"],
                            item["event"],
                            item["extra"],
                            channel,
                            item["actor_id"],
                            item["ip"],
                        )
                    except Exception:  # one broken channel must not kill the worker or skip the others
                        accounts.audit(
                            conn,
                            "notification_failed",
                            user_id=item["actor_id"],
                            target=item["topic"]["key"],
                            detail=f"{channel['channel']} {item['trigger']} failed: internal",
                            ip=item["ip"],
                        )
        except Exception:
            pass  # the worker must never die; the next item gets its own connection
        finally:
            _queue.task_done()


def flush(timeout: float = 10.0) -> None:
    """Wait until everything queued so far is delivered (for the tests)."""
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)


def reset_state() -> None:
    """Forget the rate-limit timestamps (for the tests)."""
    with _cooldown_lock:
        _last_sent.clear()


# --- test delivery from the admin UI --------------------------------------------------


def send_test(conn, topic_key: str, actor_id: int | None = None, ip: str | None = None) -> dict:
    """Send a test notification on every configured channel of the topic, synchronously, and audit it.
    Returns the outcome per channel, so the admin sees immediately whether it worked."""
    topic = _topic_row(conn, topic_key)
    if topic is None:
        from .errors import NotFound

        raise NotFound(f"Topic {topic_key!r} nicht gefunden")
    cfg = topic["notifications"]
    if not cfg:
        return {"configured": False, "channels": {}}
    results = {}
    for kind, channel_cfg in (("webhook", cfg.get("webhook")), ("email", cfg.get("email"))):
        if not channel_cfg:
            continue
        channel = {"channel": kind, "url": channel_cfg.get("url"), "to": channel_cfg.get("to")}
        results[kind] = _deliver_one(
            conn, {"key": topic_key, "title": topic["title"]}, "test", None, None, channel, actor_id, ip
        )
    return {"configured": True, "channels": results}
