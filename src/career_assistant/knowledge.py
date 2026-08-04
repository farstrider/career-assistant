from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from career_assistant.auth import set_profile_context
from career_assistant.models import (
    AssertionEvidence,
    AuditEvent,
    Evidence,
    GraphChange,
    GraphVersion,
    KGAssertion,
    KGEntity,
    KGRelation,
    KnowledgeProposal,
    OutboxEvent,
    Profile,
)

ENTITY_TYPES = {
    "Person",
    "Organization",
    "Company",
    "Recruiter",
    "Contact",
    "Role",
    "Skill",
    "Technology",
    "Project",
    "Certification",
    "Education",
    "Publication",
    "Presentation",
    "Interest",
    "Value",
    "Industry",
    "Goal",
    "JobOpportunity",
    "Application",
    "Interview",
}
RELATION_TYPES = {
    "WORKED_AT",
    "HELD_ROLE",
    "POSSESSES",
    "LEADS",
    "PRESENTED",
    "AUTHORED",
    "HOLDS",
    "STUDIED_AT",
    "INTERESTED_IN",
    "PREFERS",
    "PURSUING",
    "CONNECTED_TO",
    "APPLIED_TO",
    "INTERVIEWED_FOR",
    "RECEIVED_OFFER_FROM",
    "USES",
    "DEVELOPED_WITH",
    "SUPPORTS",
    "RELATED_TO",
    "REINFORCED_BY",
    "DEMONSTRATED_BY",
    "VALIDATED_BY",
    "BELONGS_TO",
    "EMPLOYS",
    "IS_A",
}


class KnowledgeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GraphDelta:
    object_type: str
    object_id: uuid.UUID
    operation: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def validate_entity_type(value: str) -> str:
    if value not in ENTITY_TYPES:
        raise KnowledgeError("ONTOLOGY_ENTITY_TYPE_INVALID", "Entity type is not in the ontology")
    return value


def validate_relation_type(value: str) -> str:
    if value not in RELATION_TYPES:
        raise KnowledgeError(
            "ONTOLOGY_RELATION_TYPE_INVALID", "Relation type is not in the ontology"
        )
    return value


def _now() -> datetime:
    return datetime.now(UTC)


def entity_snapshot(entity: KGEntity) -> dict[str, Any]:
    return {
        "id": str(entity.id),
        "entity_type": entity.entity_type,
        "canonical_name": entity.canonical_name,
        "normalized_name": entity.normalized_name,
        "attributes": entity.attributes,
        "retired_at": entity.retired_at.isoformat() if entity.retired_at else None,
    }


def relation_snapshot(relation: KGRelation) -> dict[str, Any]:
    return {
        "id": str(relation.id),
        "relation_type": relation.relation_type,
        "from_entity_id": str(relation.from_entity_id),
        "to_entity_id": str(relation.to_entity_id),
        "attributes": relation.attributes,
        "retired_at": relation.retired_at.isoformat() if relation.retired_at else None,
    }


def assertion_snapshot(assertion: KGAssertion) -> dict[str, Any]:
    return {
        "id": str(assertion.id),
        "subject_entity_id": str(assertion.subject_entity_id),
        "relation_id": str(assertion.relation_id) if assertion.relation_id else None,
        "predicate": assertion.predicate,
        "value": assertion.value,
        "status": assertion.status,
        "confidence": assertion.confidence,
        "confidence_method": assertion.confidence_method,
        "valid_from": assertion.valid_from.isoformat() if assertion.valid_from else None,
        "valid_until": assertion.valid_until.isoformat() if assertion.valid_until else None,
        "supersedes_id": str(assertion.supersedes_id) if assertion.supersedes_id else None,
    }


async def lock_profile(database: AsyncSession, profile_id: uuid.UUID) -> int:
    await set_profile_context(database, profile_id)
    await database.execute(select(Profile).where(Profile.id == profile_id).with_for_update())
    return int(
        await database.scalar(
            select(func.coalesce(func.max(GraphVersion.version), 0)).where(
                GraphVersion.profile_id == profile_id
            )
        )
        or 0
    )


async def commit_graph(
    database: AsyncSession,
    *,
    profile_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    actor_type: str,
    expected_version: int,
    reason: str,
    correlation_id: str,
    deltas: list[GraphDelta],
) -> int:
    current = await lock_profile(database, profile_id)
    if current != expected_version:
        raise KnowledgeError("GRAPH_VERSION_MISMATCH", "Graph changed; reload before saving")
    version = current + 1
    graph_version = GraphVersion(
        profile_id=profile_id,
        version=version,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        correlation_id=correlation_id,
    )
    database.add(graph_version)
    await database.flush()
    for delta in deltas:
        database.add(
            GraphChange(
                profile_id=profile_id,
                graph_version=version,
                object_type=delta.object_type,
                object_id=delta.object_id,
                operation=delta.operation,
                before=delta.before,
                after=delta.after,
            )
        )
    now = _now()
    database.add(
        AuditEvent(
            profile_id=profile_id,
            actor_id=actor_id,
            scope="profile",
            action=reason,
            target_type="graph_version",
            target_id=graph_version.id,
            correlation_id=correlation_id,
            metadata_={"version": version, "change_count": len(deltas)},
            occurred_at=now,
        )
    )
    database.add(
        OutboxEvent(
            topic="knowledge.graph_changed",
            aggregate_type="graph_version",
            aggregate_id=graph_version.id,
            payload={"profile_id": str(profile_id), "version": version},
            occurred_at=now,
        )
    )
    await database.commit()
    await set_profile_context(database, profile_id)
    return version


async def evidence_for_assertion(
    database: AsyncSession, assertion_id: uuid.UUID, profile_id: uuid.UUID
) -> list[Evidence]:
    return list(
        (
            await database.scalars(
                select(Evidence)
                .join(AssertionEvidence, AssertionEvidence.evidence_id == Evidence.id)
                .where(
                    AssertionEvidence.profile_id == profile_id,
                    AssertionEvidence.assertion_id == assertion_id,
                )
                .order_by(Evidence.locator)
            )
        ).all()
    )


async def proposal_for_artifact(
    database: AsyncSession,
    *,
    profile_id: uuid.UUID,
    artifact_id: uuid.UUID,
    chunks: list[tuple[str, str]],
    encrypt_excerpt: Any,
) -> int:
    """Create conservative skill proposals from explicit CV skill sections."""
    person = await database.scalar(
        select(KGEntity).where(
            KGEntity.profile_id == profile_id,
            KGEntity.entity_type == "Person",
            KGEntity.normalized_name == "self",
        )
    )
    if person is None:
        person = KGEntity(
            profile_id=profile_id,
            entity_type="Person",
            canonical_name="Profile owner",
            normalized_name="self",
            attributes={},
        )
        database.add(person)
        await database.flush()
    in_skills = False
    candidates: list[tuple[str, str, str]] = []
    for locator, text in chunks:
        heading = text.strip().rstrip(":").casefold()
        if heading in {"skills", "technical skills", "technologies", "tools"}:
            in_skills = True
            continue
        if in_skills and heading in {
            "experience",
            "work experience",
            "employment",
            "education",
            "projects",
            "certifications",
        }:
            in_skills = False
        if in_skills:
            for item in re.split(r"[,;|•]|\s+-\s+", text):
                item = item.strip(" -*\t")
                if 1 < len(item) <= 80 and re.search(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff]", item):
                    candidates.append((item, locator, text))
    created = 0
    for skill_name, locator, excerpt in candidates[:100]:
        entity = await database.scalar(
            select(KGEntity).where(
                KGEntity.profile_id == profile_id,
                KGEntity.entity_type.in_(["Skill", "Technology"]),
                KGEntity.normalized_name == normalize_name(skill_name),
            )
        )
        if entity is None:
            entity = KGEntity(
                profile_id=profile_id,
                entity_type="Skill",
                canonical_name=skill_name,
                normalized_name=normalize_name(skill_name),
                attributes={},
            )
            database.add(entity)
            await database.flush()
        evidence = Evidence(
            profile_id=profile_id,
            kind="artifact",
            source_uri=f"artifact://{artifact_id}",
            title="Imported CV",
            content_hash=normalize_name(excerpt).encode().hex()[:64].ljust(64, "0"),
            encrypted_excerpt=encrypt_excerpt(excerpt.encode("utf-8")),
            observed_at=_now(),
            metadata_={"artifact_id": str(artifact_id)},
            locator=locator,
            artifact_id=artifact_id,
        )
        database.add(evidence)
        await database.flush()
        assertion = KGAssertion(
            profile_id=profile_id,
            subject_entity_id=person.id,
            predicate="POSSESSES",
            value={"entity_id": str(entity.id), "skill": skill_name},
            status="pending",
            confidence=0.7,
            confidence_method="artifact_section_heuristic_v1",
            created_by=None,
        )
        database.add(assertion)
        await database.flush()
        database.add(
            AssertionEvidence(
                profile_id=profile_id,
                assertion_id=assertion.id,
                evidence_id=evidence.id,
                support="supports",
                weight=1.0,
                locator=locator,
            )
        )
        database.add(
            KnowledgeProposal(
                profile_id=profile_id,
                proposed_assertion_id=assertion.id,
                state="pending",
                base_graph_version=await current_graph_version(database, profile_id),
            )
        )
        created += 1
    await database.flush()
    return created


async def current_graph_version(database: AsyncSession, profile_id: uuid.UUID) -> int:
    return int(
        await database.scalar(
            select(func.coalesce(func.max(GraphVersion.version), 0)).where(
                GraphVersion.profile_id == profile_id
            )
        )
        or 0
    )


async def restore_object(
    database: AsyncSession, profile_id: uuid.UUID, object_type: str, snapshot: dict[str, Any] | None
) -> None:
    if snapshot is None:
        return
    object_id = uuid.UUID(snapshot["id"])
    if object_type == "entity":
        item: Any = await database.get(KGEntity, object_id)
        if item:
            item.retired_at = None if snapshot.get("retired_at") is None else _now()
            item.canonical_name = snapshot["canonical_name"]
            item.normalized_name = snapshot["normalized_name"]
            item.attributes = snapshot["attributes"]
    elif object_type == "relation":
        item = await database.get(KGRelation, object_id)
        if item:
            item.retired_at = None if snapshot.get("retired_at") is None else _now()
            item.attributes = snapshot["attributes"]
    elif object_type == "assertion":
        item = await database.get(KGAssertion, object_id)
        if item:
            item.status = snapshot["status"]
            item.value = snapshot["value"]
            item.confidence = snapshot["confidence"]
