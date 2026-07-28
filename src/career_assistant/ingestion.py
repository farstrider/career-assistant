from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime

from career_assistant.connectors import (
    Connector,
    ConnectorError,
    FeedConnector,
    PolicyError,
    RateLimitError,
    SchemaDriftError,
)
from career_assistant.jobs import current_cursor, ingest_item
from career_assistant.models import ConnectorRun, Operation, Source
from career_assistant.services import Services

logger = logging.getLogger(__name__)


def policy_allows(source: Source, now: datetime | None = None) -> None:
    current = now or datetime.now(UTC)
    if (
        not source.enabled
        or source.policy_status != "approved"
        or source.policy_reviewed_at is None
        or source.terms_reviewed_at is None
        or source.next_review_at is None
        or source.policy_reviewed_at.utcoffset() is None
        or source.terms_reviewed_at.utcoffset() is None
        or source.next_review_at.utcoffset() is None
        or source.next_review_at <= current
        or source.requests_per_minute < 1
    ):
        raise PolicyError("Source is disabled or lacks a current approved policy")


def connector_for(source: Source) -> Connector:
    if source.kind != "feed":
        raise SchemaDriftError("No connector is registered for this source kind")
    url = source.config.get("feed_url")
    company = source.config.get("company_name")
    if not isinstance(url, str) or not isinstance(company, str) or not company.strip():
        raise SchemaDriftError("Feed source configuration is incomplete")
    return FeedConnector(url, company)


async def execute_run(
    services: Services,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    operation_id: uuid.UUID | None = None,
    connector: Connector | None = None,
) -> None:
    async with services.sessions() as database:
        source = await database.get(Source, source_id)
        run = await database.get(ConnectorRun, run_id)
        operation = await database.get(Operation, operation_id) if operation_id else None
        if source is None or run is None:
            return
        try:
            policy_allows(source)
            active_connector = connector or connector_for(source)
            cursor = await current_cursor(database, source.id)
            run.cursor_before = cursor
            run.status = "running"
            if operation:
                operation.state = "running"
            await database.commit()

            while True:
                if isinstance(active_connector, FeedConnector):
                    allowed = await services.redis.set(
                        f"source:rate:{source.id}",
                        "1",
                        ex=max(1, math.ceil(60 / source.requests_per_minute)),
                        nx=True,
                    )
                    if not allowed:
                        raise RateLimitError("Source request budget is exhausted")
                page = await active_connector.fetch(cursor)
                for item in page.items:
                    outcome = await ingest_item(database, source, run.id, item)
                    run.fetched_count += 1
                    if outcome == "new":
                        run.new_count += 1
                    elif outcome == "changed":
                        run.changed_count += 1
                    else:
                        run.unchanged_count += 1
                    if operation:
                        operation.progress = {
                            "fetched": run.fetched_count,
                            "new": run.new_count,
                            "changed": run.changed_count,
                        }
                    await database.commit()
                cursor = page.next_cursor
                if not page.has_more:
                    break

            run.status = "succeeded"
            run.cursor_after = cursor
            run.finished_at = datetime.now(UTC)
            if operation:
                operation.state = "succeeded"
                operation.progress = {
                    "fetched": run.fetched_count,
                    "new": run.new_count,
                    "changed": run.changed_count,
                    "unchanged": run.unchanged_count,
                }
            await database.commit()
        except Exception as error:
            await database.rollback()
            run = await database.get(ConnectorRun, run_id)
            source = await database.get(Source, source_id)
            operation = await database.get(Operation, operation_id) if operation_id else None
            code = error.code if isinstance(error, ConnectorError) else "SOURCE_RUN_FAILED"
            detail = str(error)[:500] or "Source run failed"
            if run:
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.error_code = code
                run.error_detail = detail
            if source and isinstance(error, SchemaDriftError):
                source.enabled = False
                source.version += 1
            if operation:
                operation.state = "failed"
                operation.problem_code = code
                operation.problem_detail = detail
            await database.commit()
            logger.warning(
                "source_run_failed",
                extra={"source_id": str(source_id), "run_id": str(run_id), "error_code": code},
            )
