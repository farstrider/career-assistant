from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from career_assistant.connectors import RawItem
from career_assistant.models import (
    Job,
    JobFingerprint,
    JobSourceLink,
    JobVersion,
    RawSourceDocument,
    Source,
)

REMOTE_POLICIES = {
    "onsite",
    "hybrid",
    "remote_country",
    "remote_region",
    "remote_global",
    "unspecified",
}
TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise ValueError("job URL must be an unauthenticated HTTP(S) URL")
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
        )
    )
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit(
        (parsed.scheme.lower(), parsed.hostname.lower() + port, parsed.path or "/", query, "")
    )


def _clean(value: object) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def _date(value: object) -> str | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            parsed = parsedate_to_datetime(cleaned)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class NormalizedJob:
    data: dict[str, object]
    provenance: dict[str, object]
    normalized_hash: str
    fingerprints: dict[str, str]


@dataclass(frozen=True)
class IngestResult:
    outcome: Literal["new", "changed", "unchanged"]
    job_version_id: uuid.UUID | None


def normalize(source: Source, item: RawItem) -> NormalizedJob:
    fields = item.fields
    data: dict[str, object] = {
        "url": canonical_url(str(fields["url"])),
        "company_name": _clean(fields.get("company_name")),
        "title": _clean(fields.get("title")),
        "location": _clean(fields.get("location")),
        "remote_policy": _clean(fields.get("remote_policy")) or "unspecified",
        "employment_type": _clean(fields.get("employment_type")),
        "posting_date": _date(fields.get("posting_date")),
        "description": _clean(fields.get("description")),
        "skills": fields.get("skills") if isinstance(fields.get("skills"), list) else [],
        "responsibilities": fields.get("responsibilities")
        if isinstance(fields.get("responsibilities"), list)
        else [],
        "benefits": fields.get("benefits") if isinstance(fields.get("benefits"), list) else [],
    }
    if not data["company_name"] or not data["title"] or not data["description"]:
        raise ValueError("job is missing company, title, or description")
    if data["remote_policy"] not in REMOTE_POLICIES:
        data["remote_policy"] = "unspecified"
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_identity = "\0".join(
        str(data[key]).casefold() for key in ("company_name", "title", "location", "description")
    )

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    provenance: dict[str, object] = {
        key: {
            "origin": "source",
            "raw_external_id": item.external_id,
            "locator": f"{item.locator_prefix}.{key}",
        }
        for key in data
    }
    return NormalizedJob(
        data,
        provenance,
        digest(canonical),
        {
            "external": digest(f"{source.key}\0{item.external_id}"),
            "url": digest(str(data["url"])),
            "content": digest(content_identity),
        },
    )


async def ingest_item(
    database: AsyncSession, source: Source, run_id: uuid.UUID, item: RawItem
) -> IngestResult:
    normalized = normalize(source, item)
    raw_hash = hashlib.sha256(item.body).hexdigest()
    raw = await database.scalar(
        select(RawSourceDocument).where(
            RawSourceDocument.source_id == source.id,
            RawSourceDocument.content_hash == raw_hash,
        )
    )
    if raw is None:
        raw = RawSourceDocument(
            source_id=source.id,
            run_id=run_id,
            external_id=item.external_id,
            url=item.url,
            fetched_at=item.fetched_at,
            http_status=item.http_status,
            content_type=item.content_type,
            content_encoding=item.content_encoding,
            content_hash=raw_hash,
            body=item.body,
            headers=item.headers,
        )
        database.add(raw)
        await database.flush()

    link = await database.scalar(
        select(JobSourceLink).where(
            JobSourceLink.source_id == source.id,
            JobSourceLink.external_id == item.external_id,
        )
    )
    job = await database.get(Job, link.job_id) if link else None
    if job is None:
        fingerprint = await database.scalar(
            select(JobFingerprint).where(
                JobFingerprint.kind.in_(("url", "content")),
                JobFingerprint.value.in_(
                    (normalized.fingerprints["url"], normalized.fingerprints["content"])
                ),
            )
        )
        job = await database.get(Job, fingerprint.job_id) if fingerprint else None

    outcome: Literal["new", "changed", "unchanged"] = "unchanged"
    job_version_id: uuid.UUID | None = None
    if job is None:
        job = Job(
            canonical_url=str(normalized.data["url"]),
            company_name=str(normalized.data["company_name"]),
            title=str(normalized.data["title"]),
            location_text=_clean(normalized.data["location"]),
            remote_policy=str(normalized.data["remote_policy"]),
            employment_type=_clean(normalized.data["employment_type"]),
            status="open",
            discovered_at=item.fetched_at,
            posting_date=datetime.fromisoformat(str(normalized.data["posting_date"]))
            if normalized.data["posting_date"]
            else None,
        )
        database.add(job)
        await database.flush()
        outcome = "new"

    if link is None:
        database.add(
            JobSourceLink(
                job_id=job.id,
                source_id=source.id,
                external_id=item.external_id,
                url=item.url,
                first_seen_at=item.fetched_at,
                last_seen_at=item.fetched_at,
            )
        )
    else:
        link.last_seen_at = item.fetched_at
        link.url = item.url

    locked_job = await database.scalar(select(Job).where(Job.id == job.id).with_for_update())
    assert locked_job is not None
    job = locked_job
    current = await database.scalar(
        select(JobVersion).where(JobVersion.job_id == job.id).order_by(JobVersion.version.desc())
    )
    if current is None or current.normalized_hash != normalized.normalized_hash:
        version = 1 if current is None else current.version + 1
        version_item = JobVersion(
            job_id=job.id,
            version=version,
            normalized_data=normalized.data,
            field_provenance=normalized.provenance,
            normalized_hash=normalized.normalized_hash,
            raw_document_id=raw.id,
            valid_from=item.fetched_at,
        )
        database.add(version_item)
        await database.flush()
        job_version_id = version_item.id
        job.canonical_url = str(normalized.data["url"])
        job.company_name = str(normalized.data["company_name"])
        job.title = str(normalized.data["title"])
        job.location_text = _clean(normalized.data["location"])
        job.remote_policy = str(normalized.data["remote_policy"])
        job.employment_type = _clean(normalized.data["employment_type"])
        if outcome != "new":
            outcome = "changed"

    existing = set(
        (
            await database.execute(
                select(JobFingerprint.kind, JobFingerprint.value).where(
                    JobFingerprint.kind.in_(tuple(normalized.fingerprints)),
                    JobFingerprint.value.in_(tuple(normalized.fingerprints.values())),
                )
            )
        ).all()
    )
    for kind, value in normalized.fingerprints.items():
        if (kind, value) not in existing:
            database.add(JobFingerprint(job_id=job.id, kind=kind, value=value, strength="exact"))
    return IngestResult(outcome, job_version_id)


async def current_cursor(database: AsyncSession, source_id: uuid.UUID) -> str | None:
    from career_assistant.models import ConnectorRun

    return await database.scalar(
        select(ConnectorRun.cursor_after)
        .where(ConnectorRun.source_id == source_id, ConnectorRun.status == "succeeded")
        .order_by(ConnectorRun.finished_at.desc())
        .limit(1)
    )
