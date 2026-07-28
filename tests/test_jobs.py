from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from career_assistant.connectors import (
    FeedConnector,
    RateLimitError,
    SchemaDriftError,
    UnsafeUrlError,
    parse_feed,
)
from career_assistant.ingestion import PolicyError, policy_allows
from career_assistant.jobs import canonical_url, normalize
from career_assistant.models import Source

FEED = b"""<?xml version="1.0"?>
<rss><channel><item>
  <guid>job-1</guid>
  <title>Platform Engineering Manager</title>
  <link>https://jobs.example/jobs/1?utm_source=test</link>
  <description>Lead a multilingual platform team.</description>
  <location>Tokyo</location>
</item></channel></rss>"""


def test_feed_normalization_is_deterministic_and_traces_every_field() -> None:
    item = parse_feed(FEED, "https://jobs.example/feed", "Example 株式会社")[0]
    source = Source(key="example", kind="feed", acquisition_method="official_feed")
    first = normalize(source, item)
    second = normalize(source, item)

    assert first == second
    assert first.data["url"] == "https://jobs.example/jobs/1"
    assert first.data["company_name"] == "Example 株式会社"
    assert item.body == FEED
    assert set(first.provenance) == set(first.data)
    assert all(value["raw_external_id"] == "job-1" for value in first.provenance.values())  # type: ignore[index]


def test_feed_rejects_entities_and_missing_required_fields() -> None:
    with pytest.raises(SchemaDriftError):
        parse_feed(b"<!DOCTYPE rss [<!ENTITY x 'bad'>]><rss>&x;</rss>", "https://x", "X")
    with pytest.raises(SchemaDriftError):
        parse_feed(
            b"<rss><channel><item><title>Missing URL</title></item></channel></rss>",
            "https://x",
            "X",
        )


@pytest.mark.asyncio
async def test_feed_validates_every_redirect_and_blocks_private_destinations() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    async def resolver(host: str, _: int) -> list[str]:
        if host == "127.0.0.1":
            raise UnsafeUrlError("blocked")
        return ["203.0.113.10"]

    connector = FeedConnector(
        "https://jobs.example/feed",
        "Example",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=resolver,
    )
    with pytest.raises(UnsafeUrlError):
        await connector.fetch(None)
    await connector.client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_feed_honors_bounded_retry_after() -> None:
    sleeps: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, request=request)

    async def resolver(_: str, __: int) -> list[str]:
        return ["203.0.113.10"]

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    connector = FeedConnector(
        "https://jobs.example/feed",
        "Example",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=resolver,
        sleep=sleep,
    )
    with pytest.raises(RateLimitError):
        await connector.fetch(None)
    assert sleeps == [7, 7]
    await connector.client.aclose()  # type: ignore[union-attr]


def test_policy_gate_requires_enabled_current_approval_and_rate_budget() -> None:
    now = datetime.now(UTC)
    source = Source(
        key="example",
        kind="feed",
        enabled=True,
        acquisition_method="official_feed",
        policy_status="approved",
        policy_reviewed_at=now - timedelta(days=1),
        terms_reviewed_at=now - timedelta(days=1),
        next_review_at=now + timedelta(days=1),
        requests_per_minute=1,
        config={},
    )
    policy_allows(source, now)
    source.next_review_at = now
    with pytest.raises(PolicyError):
        policy_allows(source, now)


def test_canonical_url_rejects_credentials_and_unsafe_schemes() -> None:
    assert canonical_url("https://EXAMPLE.com/jobs/1?utm_campaign=x&a=1") == (
        "https://example.com/jobs/1?a=1"
    )
    with pytest.raises(ValueError):
        canonical_url("file:///etc/passwd")
    with pytest.raises(ValueError):
        canonical_url("https://user@example.com/jobs")
