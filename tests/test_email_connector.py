from __future__ import annotations

import imaplib
import json
import ssl
from datetime import UTC, datetime
from pathlib import Path

import pytest

from career_assistant.connectors import (
    AlertEmailConnector,
    AuthenticationError,
    ContentError,
    ImapClient,
    SchemaDriftError,
    parse_linkedin_alert,
)

FIXTURE = Path(__file__).parent / "fixtures" / "linkedin-alert.eml"
SENDERS = frozenset({"jobalerts-noreply@linkedin.com"})
HOSTS = frozenset({"www.linkedin.com"})
TEST_APP_PASSWORD = "test-only-app-password"  # pragma: allowlist secret


def test_linkedin_digest_extracts_complete_jobs_without_tracking_data() -> None:
    items = parse_linkedin_alert(FIXTURE.read_bytes(), datetime.now(UTC), SENDERS, HOSTS)

    assert len(items) == 6
    assert items[0].external_id == "100001"
    assert items[0].url == "https://www.linkedin.com/jobs/view/100001/"
    assert items[0].fields["company_name"] == "RevenueCat"
    assert items[0].fields["location"] == "Japan"
    assert items[0].fields["remote_policy"] == "remote_country"
    assert items[2].fields["title"] == "情報セキュリティ・サイバーセキュリティ監査コンサルタント"
    assert all("trackingId" not in item.url for item in items)
    assert b"trackingId" not in items[0].body
    assert b"alerts@example.invalid" not in items[0].body
    assert b"remote.example.invalid" not in items[0].body
    assert set(items[0].headers) == {"message-id", "date", "from", "subject"}


def test_linkedin_digest_rejects_untrusted_or_incomplete_messages() -> None:
    spoofed = FIXTURE.read_bytes().replace(
        b"jobalerts-noreply@linkedin.com", b"attacker@example.invalid", 1
    )
    assert parse_linkedin_alert(spoofed, datetime.now(UTC), SENDERS, HOSTS) == []

    incomplete = FIXTURE.read_bytes().replace(b"RevenueCat \xc2\xb7 Japan (Remote)", b"Japan")
    with pytest.raises(SchemaDriftError, match="company"):
        parse_linkedin_alert(incomplete, datetime.now(UTC), SENDERS, HOSTS)

    attachment = FIXTURE.read_bytes().replace(
        b'Content-Type: text/plain; charset="utf-8"',
        b'Content-Type: text/plain; charset="utf-8"\nContent-Disposition: attachment',
    )
    with pytest.raises(ContentError, match="attachments"):
        parse_linkedin_alert(attachment, datetime.now(UTC), SENDERS, HOSTS)


class FakeImap(ImapClient):
    def __init__(self, body: bytes, *, auth_failure: bool = False) -> None:
        self.body = body
        self.auth_failure = auth_failure
        self.calls: list[tuple[object, ...]] = []

    def login(self, username: str, password: str) -> tuple[str, list[bytes]]:
        self.calls.append(("login", username, password))
        if self.auth_failure:
            raise imaplib.IMAP4.error("bad credentials")
        return "OK", [b""]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"2"]

    def response(self, code: str) -> tuple[str, list[bytes] | None]:
        return code, [b"77"]

    def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
        self.calls.append(("uid", command, *args))
        if command == "search":
            return "OK", [b"10 11"]
        return "OK", [(b"10 (RFC822)", self.body)]

    def logout(self) -> tuple[str, list[bytes]]:
        self.calls.append(("logout",))
        return "BYE", [b""]


def test_gmail_connector_uses_tls_uid_cursor_and_bounded_pages() -> None:
    fake = FakeImap(FIXTURE.read_bytes())

    def factory(host: str, port: int, context: ssl.SSLContext, timeout: float) -> ImapClient:
        assert (host, port, timeout) == ("imap.gmail.com", 993, 9)
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
        return fake

    connector = AlertEmailConnector(
        "imap.gmail.com",
        993,
        "alerts@example.invalid",
        TEST_APP_PASSWORD,
        "Career Alerts",
        list(SENDERS),
        list(HOSTS),
        timeout=9,
        batch_size=1,
        client_factory=factory,
    )
    page = connector._fetch(json.dumps({"uid_validity": "77", "last_uid": 9}))

    assert len(page.items) == 6
    assert page.has_more
    assert json.loads(page.next_cursor or "") == {"uid_validity": "77", "last_uid": 10}
    assert ("select", "Career Alerts", True) in fake.calls
    assert ("uid", "search", "UID", "10:*") in fake.calls


def test_gmail_connector_classifies_authentication_failure() -> None:
    fake = FakeImap(FIXTURE.read_bytes(), auth_failure=True)
    connector = AlertEmailConnector(
        "imap.gmail.com",
        993,
        "alerts@example.invalid",
        TEST_APP_PASSWORD,
        "Career Alerts",
        list(SENDERS),
        list(HOSTS),
        client_factory=lambda *_: fake,
    )

    with pytest.raises(AuthenticationError):
        connector._fetch(None)
