from __future__ import annotations

import asyncio
import imaplib
import ipaddress
import json
import re
import socket
import ssl
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Protocol, cast
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
SAFE_HEADERS = {"content-type", "etag", "last-modified"}


class ConnectorError(Exception):
    code = "CONNECTOR_FAILED"


class PolicyError(ConnectorError):
    code = "SOURCE_POLICY_BLOCKED"


class SchemaDriftError(ConnectorError):
    code = "SOURCE_SCHEMA_DRIFT"


class UnsafeUrlError(ConnectorError):
    code = "SOURCE_URL_BLOCKED"


class RateLimitError(ConnectorError):
    code = "SOURCE_RATE_LIMITED"


class AuthenticationError(ConnectorError):
    code = "SOURCE_AUTHENTICATION_FAILED"


class ContentError(ConnectorError):
    code = "SOURCE_CONTENT_REJECTED"


@dataclass(frozen=True)
class RawItem:
    external_id: str
    url: str
    fetched_at: datetime
    content_type: str
    body: bytes
    headers: dict[str, str]
    fields: dict[str, object]
    locator_prefix: str = "$"
    http_status: int = 200
    content_encoding: str | None = None


@dataclass(frozen=True)
class FetchPage:
    items: list[RawItem]
    next_cursor: str | None
    has_more: bool = False
    retry_after: int | None = None


class Connector(Protocol):
    key: str

    async def fetch(self, cursor: str | None) -> FetchPage: ...


Resolver = Callable[[str, int], Awaitable[list[str]]]
Sleeper = Callable[[float], Awaitable[None]]


class ImapClient(Protocol):
    def login(self, username: str, password: str) -> tuple[str, list[bytes]]: ...

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]: ...

    def response(self, code: str) -> tuple[str, list[bytes] | None]: ...

    def uid(self, command: str, *args: str) -> tuple[str, list[object]]: ...

    def logout(self) -> tuple[str, list[bytes]]: ...


ImapFactory = Callable[[str, int, ssl.SSLContext, float], ImapClient]


def _imap_client(host: str, port: int, context: ssl.SSLContext, timeout: float) -> ImapClient:
    return cast(
        ImapClient,
        imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=timeout),
    )


async def resolve_public(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    addresses = {item[4][0] for item in await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise UnsafeUrlError("Source host does not resolve exclusively to public addresses")
    return sorted(addresses)


async def validate_public_url(url: str, resolver: Resolver = resolve_public) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise UnsafeUrlError("Source URL must be an unauthenticated HTTP(S) URL")
    await resolver(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))


def _retry_seconds(response: httpx.Response) -> int:
    value = response.headers.get("Retry-After", "")
    if value.isdigit():
        return min(int(value), 300)
    try:
        return max(
            0, min(300, int((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()))
        )
    except (TypeError, ValueError):
        return 1


class FeedConnector:
    key = "feed"

    def __init__(
        self,
        url: str,
        company_name: str,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver = resolve_public,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self.url = url
        self.company_name = company_name
        self.client = client
        self.resolver = resolver
        self.sleep = sleep

    async def fetch(self, cursor: str | None) -> FetchPage:
        headers = {"Accept": "application/atom+xml, application/rss+xml, application/xml"}
        if cursor:
            previous = json.loads(cursor)
            if previous.get("etag"):
                headers["If-None-Match"] = previous["etag"]
            if previous.get("last_modified"):
                headers["If-Modified-Since"] = previous["last_modified"]
        response = await self._request(headers)
        if response.status_code == 304:
            return FetchPage([], cursor)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type not in {
            "application/atom+xml",
            "application/rss+xml",
            "application/xml",
            "text/xml",
        }:
            raise SchemaDriftError("Feed returned an unsupported content type")
        body = response.content
        if len(body) > MAX_RESPONSE_BYTES:
            raise ConnectorError("Feed response exceeds the size limit")
        compressed = int(response.headers.get("Content-Length", len(body)) or len(body))
        if compressed and len(body) > compressed * 100:
            raise ConnectorError("Feed decompression ratio exceeds the limit")
        items = parse_feed(body, str(response.url), self.company_name, response.headers)
        for item in items:
            await validate_public_url(item.url, self.resolver)
        next_cursor = json.dumps(
            {
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            },
            sort_keys=True,
        )
        return FetchPage(items, next_cursor)

    async def _request(self, headers: dict[str, str]) -> httpx.Response:
        client = self.client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        owns_client = self.client is None
        url = self.url
        try:
            for attempt in range(3):
                for _ in range(MAX_REDIRECTS + 1):
                    await validate_public_url(url, self.resolver)
                    try:
                        response = await client.get(url, headers=headers)
                    except httpx.TransportError:
                        if attempt == 2:
                            raise ConnectorError("Feed request failed") from None
                        await self.sleep(2**attempt)
                        break
                    if response.is_redirect:
                        location = response.headers.get("Location")
                        if not location:
                            raise ConnectorError("Feed redirect has no destination")
                        url = urljoin(url, location)
                        continue
                    if response.status_code == 429:
                        if attempt == 2:
                            raise RateLimitError("Feed rate limit retry budget exhausted")
                        await self.sleep(_retry_seconds(response))
                        break
                    if response.status_code >= 500:
                        if attempt == 2:
                            raise ConnectorError("Feed server retry budget exhausted")
                        await self.sleep(2**attempt)
                        break
                    response.raise_for_status()
                    return response
                else:
                    raise UnsafeUrlError("Feed redirect limit exceeded")
            raise ConnectorError("Feed request failed")
        finally:
            if owns_client:
                await client.aclose()


def _text(element: ElementTree.Element, names: tuple[str, ...]) -> str | None:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1] in names and child.text and child.text.strip():
            return child.text.strip()
    return None


def parse_feed(
    body: bytes, base_url: str, company_name: str, headers: httpx.Headers | None = None
) -> list[RawItem]:
    upper = body.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SchemaDriftError("Feed XML declarations are not allowed")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise SchemaDriftError("Feed XML could not be parsed") from error
    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
    if not entries:
        raise SchemaDriftError("Feed contains no job entries")
    fetched_at = datetime.now(UTC)
    safe_headers = {
        key.lower(): value for key, value in (headers or {}).items() if key.lower() in SAFE_HEADERS
    }
    items: list[RawItem] = []
    for index, entry in enumerate(entries):
        link = _text(entry, ("link",))
        if link is None:
            link_node = next(
                (child for child in entry.iter() if child.tag.rsplit("}", 1)[-1] == "link"),
                None,
            )
            link = link_node.get("href") if link_node is not None else None
        title = _text(entry, ("title",))
        description = _text(entry, ("description", "summary", "content"))
        external_id = _text(entry, ("guid", "id")) or link
        if not title or not link or not external_id or description is None:
            raise SchemaDriftError(f"Feed entry {index + 1} is missing required fields")
        url = urljoin(base_url, link)
        fields: dict[str, object] = {
            "external_id": external_id,
            "url": url,
            "company_name": company_name,
            "title": title,
            "description": description,
            "location": _text(entry, ("location",)),
            "posting_date": _text(entry, ("published", "pubDate", "updated")),
        }
        items.append(
            RawItem(
                external_id=external_id,
                url=url,
                fetched_at=fetched_at,
                content_type="application/xml",
                body=body,
                headers=safe_headers,
                fields=fields,
                locator_prefix=f"feed.entries[{index}]",
            )
        )
    return items


@dataclass(frozen=True)
class _HtmlToken:
    href: str | None
    text: str


class _VisibleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[_HtmlToken] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        elif tag == "a" and not self._ignored_depth:
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag == "a" and self._href is not None:
            text = " ".join("".join(self._link_text).split())
            self.tokens.append(_HtmlToken(self._href, text))
            self._href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._href is not None:
            self._link_text.append(text)
        else:
            self.tokens.append(_HtmlToken(None, text))


_LINKEDIN_JOB_PATH = re.compile(r"/(?:comm/)?jobs/view/(\d+)(?:/|$)")


def _linkedin_job_id(href: str, allowed_hosts: frozenset[str]) -> str | None:
    parsed = urlsplit(href)
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower() not in allowed_hosts
    ):
        return None
    match = _LINKEDIN_JOB_PATH.search(parsed.path)
    return match.group(1) if match else None


def parse_linkedin_alert(
    body: bytes,
    fetched_at: datetime,
    allowed_senders: frozenset[str],
    allowed_hosts: frozenset[str],
) -> list[RawItem]:
    if len(body) > MAX_RESPONSE_BYTES:
        raise ContentError("Email exceeds the size limit")
    message = BytesParser(policy=policy.default).parsebytes(body)
    if message.defects:
        raise ContentError("Email MIME structure is malformed")
    senders = {
        address.casefold() for _, address in getaddresses(message.get_all("From", [])) if address
    }
    if len(senders) != 1 or not senders.issubset(allowed_senders):
        return []
    if not message.get("Message-ID"):
        raise ContentError("Email has no Message-ID")

    html: str | None = None
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            raise ContentError("Email attachments are not accepted")
        if part.get_content_type() == "text/html":
            try:
                html = part.get_content()
            except (LookupError, UnicodeError) as error:
                raise ContentError("Email HTML could not be decoded") from error
    if html is None:
        raise SchemaDriftError("LinkedIn alert has no HTML body")

    parser = _VisibleHtmlParser()
    parser.feed(html)
    candidates: list[tuple[int, str, str]] = []
    for index, token in enumerate(parser.tokens):
        job_id = _linkedin_job_id(token.href, allowed_hosts) if token.href else None
        if job_id and token.text:
            candidates.append((index, job_id, token.text))

    parsed_jobs: list[dict[str, object]] = []
    seen: set[str] = set()
    safe_headers = {
        key.lower(): str(message[key])
        for key in ("Message-ID", "Date", "From", "Subject")
        if message.get(key)
    }
    for index, job_id, title in candidates:
        if job_id in seen:
            continue
        visible: list[str] = []
        for token in parser.tokens[index + 1 :]:
            if token.href and _linkedin_job_id(token.href, allowed_hosts):
                break
            if token.href is None:
                visible.append(token.text)
        company_line = next((text for text in visible if "·" in text), None)
        if company_line is None:
            raise SchemaDriftError(f"LinkedIn job {job_id} has no company and location")
        company, location = (value.strip() for value in company_line.split("·", 1))
        if not company or not location:
            raise SchemaDriftError(f"LinkedIn job {job_id} has incomplete company data")
        remote_policy = "unspecified"
        if location.casefold().endswith("(remote)"):
            location = location[: -len("(remote)")].strip()
            remote_policy = "remote_country"
        metadata = next(
            (
                text
                for text in visible[visible.index(company_line) + 1 :]
                if text == "Actively recruiting"
                or text.endswith(" company alumni")
                or re.match(r"^[¥$€£].+/(?:year|month|hour)$", text, re.IGNORECASE)
            ),
            None,
        )
        description_parts = [company_line]
        if metadata:
            description_parts.append(metadata)
        description = " | ".join(dict.fromkeys(description_parts))
        url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        parsed_jobs.append(
            {
                "external_id": job_id,
                "url": url,
                "company_name": company,
                "title": title,
                "description": description,
                "location": location,
                "remote_policy": remote_policy,
            }
        )
        seen.add(job_id)
    if not parsed_jobs:
        raise SchemaDriftError("LinkedIn alert contains no recognized job cards")

    retained_body = json.dumps(
        {"headers": safe_headers, "jobs": parsed_jobs},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    items: list[RawItem] = []
    for index, fields in enumerate(parsed_jobs):
        job_id = str(fields["external_id"])
        url = str(fields["url"])
        items.append(
            RawItem(
                external_id=job_id,
                url=url,
                fetched_at=fetched_at,
                content_type="application/json",
                body=retained_body,
                headers=safe_headers,
                fields=fields,
                locator_prefix=f"email.jobs[{index}]",
            )
        )
    return items


class AlertEmailConnector:
    key = "alert_email"

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        mailbox: str,
        sender_allowlist: list[str],
        link_host_allowlist: list[str],
        *,
        timeout: float = 20,
        batch_size: int = 50,
        client_factory: ImapFactory = _imap_client,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.mailbox = mailbox
        self.allowed_senders = frozenset(value.casefold() for value in sender_allowlist)
        self.allowed_hosts = frozenset(value.casefold() for value in link_host_allowlist)
        self.timeout = timeout
        self.batch_size = batch_size
        self.client_factory = client_factory

    async def fetch(self, cursor: str | None) -> FetchPage:
        return await asyncio.to_thread(self._fetch, cursor)

    def _fetch(self, cursor: str | None) -> FetchPage:
        context = ssl.create_default_context()
        client = self.client_factory(self.host, self.port, context, self.timeout)
        try:
            try:
                status, _ = client.login(self.username, self.password)
            except imaplib.IMAP4.error as error:
                raise AuthenticationError("Gmail IMAP authentication failed") from error
            if status != "OK":
                raise AuthenticationError("Gmail IMAP authentication failed")
            status, _ = client.select(self.mailbox, readonly=True)
            if status != "OK":
                raise ConnectorError("Gmail alert mailbox could not be opened")
            _, validity_data = client.response("UIDVALIDITY")
            if not validity_data or not validity_data[0]:
                raise ConnectorError("Gmail mailbox returned no UIDVALIDITY")
            uid_validity = validity_data[0].decode()
            last_uid = 0
            if cursor:
                try:
                    previous = json.loads(cursor)
                    if previous.get("uid_validity") == uid_validity:
                        last_uid = int(previous.get("last_uid", 0))
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise SchemaDriftError("Email cursor is invalid") from error
            status, search_data = client.uid("search", "UID", f"{last_uid + 1}:*")
            if status != "OK":
                raise ConnectorError("Gmail mailbox search failed")
            raw_uids = search_data[0] if search_data else b""
            if not isinstance(raw_uids, bytes):
                raise ConnectorError("Gmail mailbox returned invalid UIDs")
            uids = [int(value) for value in raw_uids.split()]
            selected = uids[: self.batch_size]
            items: list[RawItem] = []
            fetched_at = datetime.now(UTC)
            for uid in selected:
                status, fetch_data = client.uid("fetch", str(uid), "(RFC822)")
                if status != "OK":
                    raise ConnectorError("Gmail message fetch failed")
                message_body = next(
                    (
                        value[1]
                        for value in fetch_data
                        if isinstance(value, tuple)
                        and len(value) > 1
                        and isinstance(value[1], bytes)
                    ),
                    None,
                )
                if message_body is None:
                    raise ConnectorError("Gmail message body is missing")
                items.extend(
                    parse_linkedin_alert(
                        message_body,
                        fetched_at,
                        self.allowed_senders,
                        self.allowed_hosts,
                    )
                )
            next_cursor = json.dumps(
                {
                    "last_uid": selected[-1] if selected else last_uid,
                    "uid_validity": uid_validity,
                },
                sort_keys=True,
            )
            return FetchPage(items, next_cursor, has_more=len(uids) > len(selected))
        finally:
            with suppress(imaplib.IMAP4.error, OSError):
                client.logout()


class ManualImportConnector:
    key = "manual"

    def __init__(self, items: list[dict[str, object]], document: bytes | None = None) -> None:
        self.items = items
        self.document = document

    async def fetch(self, cursor: str | None) -> FetchPage:
        now = datetime.now(UTC)
        raw_items: list[RawItem] = []
        for index, fields in enumerate(self.items):
            missing = [
                key
                for key in ("external_id", "url", "company_name", "title", "description")
                if not fields.get(key)
            ]
            if missing:
                raise SchemaDriftError(f"Manual item {index + 1} is missing {', '.join(missing)}")
            body = (
                self.document
                or json.dumps(
                    self.items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            )
            raw_items.append(
                RawItem(
                    external_id=str(fields["external_id"]),
                    url=str(fields["url"]),
                    fetched_at=now,
                    content_type="application/json",
                    body=body,
                    headers={},
                    fields=fields,
                    locator_prefix=f"$[{index}]",
                )
            )
        return FetchPage(raw_items, "complete")
