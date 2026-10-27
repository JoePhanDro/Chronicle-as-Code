import asyncio

import httpx2 as httpx
import pytest

from tests.conftest import app_for
from chronicle import accounts, security
from chronicle.db import connect

PASSWORD = "correct horse battery"
ATTACKER = "203.0.113.9"
OWNER = "198.51.100.7"
PROXY = "10.0.0.1"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")
    monkeypatch.delenv("CHRONICLE_TRUSTED_PROXIES", raising=False)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "throttle.db"
    conn = connect(path)
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()
    return path


def login(app, source: str, username: str = "anna", password: str = PASSWORD, forwarded: str | None = None):
    """Log in as if the connection came from `source`."""
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    async def post():
        transport = httpx.ASGITransport(app=app, client=(source, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=headers)

    return asyncio.run(post())


def fail_five_times(app, source: str, forwarded: str | None = None):
    for _ in range(5):
        assert login(app, source, password="falsch", forwarded=forwarded).status_code == 401


def test_attacker_cannot_lock_the_owner_out(db_path):
    app = app_for(db_path, trusted_proxies="")
    fail_five_times(app, ATTACKER)

    assert login(app, ATTACKER).status_code == 429  # the attacker is locked for this account
    assert login(app, OWNER).status_code == 200  # the owner from another address still gets in


def test_failures_for_many_accounts_lock_only_that_address(db_path):
    app = app_for(db_path, trusted_proxies="")
    for index in range(20):
        assert login(app, ATTACKER, username=f"niemand{index}", password="falsch").status_code == 401

    assert login(app, ATTACKER).status_code == 429  # the address is locked for every account
    assert login(app, OWNER).status_code == 200


def test_successful_login_clears_the_failures_of_this_client(db_path):
    app = app_for(db_path, trusted_proxies="")
    for _ in range(4):
        assert login(app, OWNER, password="falsch").status_code == 401
    assert login(app, OWNER).status_code == 200
    for _ in range(4):
        assert login(app, OWNER, password="falsch").status_code == 401
    assert login(app, OWNER).status_code == 200


def test_behind_a_trusted_proxy_the_forwarded_client_counts(db_path):
    app = app_for(db_path, trusted_proxies=PROXY)
    fail_five_times(app, PROXY, forwarded=ATTACKER)

    assert login(app, PROXY, forwarded=ATTACKER).status_code == 429
    assert login(app, PROXY, forwarded=OWNER).status_code == 200  # same proxy, different client


def test_forwarded_header_from_an_untrusted_peer_is_ignored(db_path):
    app = app_for(db_path, trusted_proxies=PROXY)
    fail_five_times(app, ATTACKER, forwarded=OWNER)  # claims to be the owner, but is not behind the proxy

    assert login(app, ATTACKER, forwarded=OWNER).status_code == 429  # still counted as the real address
    assert login(app, OWNER).status_code == 200

    conn = connect(db_path)
    failed = [entry for entry in accounts.list_audit(conn) if entry["action"] == "login_failed"]
    conn.close()
    assert {entry["ip"] for entry in failed} == {ATTACKER}


def test_without_trusted_proxies_no_forwarded_header_is_used(db_path):
    app = app_for(db_path, trusted_proxies="")
    fail_five_times(app, PROXY, forwarded=ATTACKER)

    assert login(app, PROXY, forwarded=OWNER).status_code == 429  # every request is the proxy itself


def test_trusted_proxies_come_from_the_environment(db_path, monkeypatch):
    monkeypatch.setenv("CHRONICLE_TRUSTED_PROXIES", "10.0.0.0/8")
    app = app_for(db_path)
    fail_five_times(app, "10.1.2.3", forwarded=ATTACKER)

    assert login(app, "10.1.2.3", forwarded=ATTACKER).status_code == 429
    assert login(app, "10.1.2.3", forwarded=OWNER).status_code == 200
