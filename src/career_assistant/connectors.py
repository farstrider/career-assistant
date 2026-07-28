from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
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
