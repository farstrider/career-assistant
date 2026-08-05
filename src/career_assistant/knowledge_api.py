from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select

from career_assistant.artifacts import artifact_cipher
from career_assistant.auth import Current, Database, Mutation, problem, set_profile_context
from career_assistant.knowledge import (
    RELATION_TYPES,
    GraphDelta,
    KnowledgeError,
    assertion_snapshot,
    commit_graph,
    current_graph_version,
    entity_snapshot,
    evidence_for_assertion,
    lock_profile,
    normalize_name,
    relation_snapshot,
    restore_object,
    validate_entity_type,
    validate_relation_type,
)
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
    LearningObservation,
    OutboxEvent,
)

router = APIRouter()


class EvidenceInput(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    source_uri: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=300)
    excerpt: str = Field(min_length=1, max_length=5000)
    observed_at: datetime
    locator: str = Field(min_length=1, max_length=300)

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value


class EvidenceResponse(BaseModel):
    id: uuid.UUID
    kind: str
    source_uri: str
    title: str
    excerpt: str | None
    observed_at: datetime
    locator: str
    artifact_id: uuid.UUID | None


class AssertionResponse(BaseModel):
    id: uuid.UUID
    subject_entity_id: uuid.UUID
    relation_id: uuid.UUID | None
    predicate: str
    value: dict[str, object]
    status: str
    confidence: float
    confidence_method: str
    valid_from: datetime | None
    valid_until: datetime | None
    evidence_ids: list[uuid.UUID]


class EntityResponse(BaseModel):
    id: uuid.UUID
    type: str
    canonical_name: str
    attributes: dict[str, object]
    assertions: list[AssertionResponse]
    graph_version: int


class EntityCreate(BaseModel):
    type: str
    canonical_name: str = Field(min_length=1, max_length=300)
    attributes: dict[str, object] = Field(default_factory=dict)


class RelationCreate(BaseModel):
    from_entity_id: uuid.UUID
    to_entity_id: uuid.UUID
    relation_type: str
    attributes: dict[str, object] = Field(default_factory=dict)


class AssertionCreate(BaseModel):
    subject_entity_id: uuid.UUID
    relation_id: uuid.UUID | None = None
    predicate: str = Field(min_length=1, max_length=64)
    value: dict[str, object]
    confidence: float = Field(default=1.0, ge=0, le=1)
    confidence_method: str = Field(default="user_confirmation", max_length=64)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    evidence: list[EvidenceInput] = Field(min_length=1, max_length=20)


class EntityPage(BaseModel):
    items: list[EntityResponse]
    next_cursor: str | None = None
    has_more: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    entity_types: list[str] = Field(default_factory=list, max_length=10)
    approval_states: list[str] = Field(default_factory=lambda: ["confirmed"], max_length=5)
    limit: int = Field(default=20, ge=1, le=100)


class SearchResult(BaseModel):
    entity: EntityResponse
    matched_assertion_ids: list[uuid.UUID]
    evidence_ids: list[uuid.UUID]
    text_score: float
    semantic_score: float = 0.0
    score: float


class TraverseRequest(BaseModel):
    start_entity_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    relation_types: list[str] = Field(default_factory=list, max_length=20)
    direction: Literal["in", "out", "both"] = "both"
    max_depth: int = Field(default=2, ge=1, le=4)
    max_paths: int = Field(default=50, ge=1, le=250)
    minimum_confidence: float = Field(default=0, ge=0, le=1)
    approval_states: list[str] = Field(default_factory=lambda: ["confirmed"], max_length=5)


class GraphPath(BaseModel):
    entities: list[uuid.UUID]
    relations: list[uuid.UUID]


class ProposalResponse(BaseModel):
    id: uuid.UUID
    assertion: AssertionResponse
    state: str
    base_graph_version: int
    decision_note: str | None
    current_graph_version: int
    defer_until: datetime | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    evidence: list[EvidenceResponse]
    observation_state: str | None
    observation_evidence_count: int | None


class ProposalDecision(BaseModel):
    decision: Literal["approve", "approve_with_edit", "reject", "defer"]
    value: dict[str, object] | None = None
    note: str | None = Field(default=None, max_length=2000)
    defer_until: datetime | None = None

    @field_validator("defer_until")
    @classmethod
    def defer_timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("defer_until must include a timezone")
        return value


class VersionResponse(BaseModel):
    version: int
    actor_type: str
    reason: str
    correlation_id: str
    created_at: datetime


class ChangeResponse(BaseModel):
    version: int
    object_type: str
    object_id: uuid.UUID
    operation: str
    before: dict[str, object] | None
    after: dict[str, object] | None


class RollbackRequest(BaseModel):
    target_version: int = Field(ge=0)
    confirm: Literal[True]
    reason: str = Field(min_length=1, max_length=500)


def _if_match(value: str) -> int:
    try:
        return int(value.strip('"'))
    except ValueError:
        raise problem(
            status.HTTP_400_BAD_REQUEST, "INVALID_VERSION", "If-Match is invalid"
        ) from None


def _knowledge_problem(error: KnowledgeError) -> Exception:
    status_code = (
        status.HTTP_412_PRECONDITION_FAILED
        if error.code == "GRAPH_VERSION_MISMATCH"
        else status.HTTP_409_CONFLICT
        if error.code == "PROPOSAL_STATE_INVALID"
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    return problem(status_code, error.code, str(error))


async def _assertion_response(database: Database, item: KGAssertion) -> AssertionResponse:
    evidence = await evidence_for_assertion(database, item.id, item.profile_id)
    return AssertionResponse(
        id=item.id,
        subject_entity_id=item.subject_entity_id,
        relation_id=item.relation_id,
        predicate=item.predicate,
        value=item.value,
        status=item.status,
        confidence=item.confidence,
        confidence_method=item.confidence_method,
        valid_from=item.valid_from,
        valid_until=item.valid_until,
        evidence_ids=[item.id for item in evidence],
    )


async def _assertions_for_entity(
    database: Database, profile_id: uuid.UUID, entity_id: uuid.UUID
) -> list[KGAssertion]:
    relation_ids = select(KGRelation.id).where(
        KGRelation.profile_id == profile_id,
        KGRelation.retired_at.is_(None),
        or_(KGRelation.from_entity_id == entity_id, KGRelation.to_entity_id == entity_id),
    )
    return list(
        (
            await database.scalars(
                select(KGAssertion).where(
                    KGAssertion.profile_id == profile_id,
                    or_(
                        KGAssertion.subject_entity_id == entity_id,
                        KGAssertion.relation_id.in_(relation_ids),
                    ),
                )
            )
        ).all()
    )


async def _proposal_response(
    request: Request,
    database: Database,
    proposal: KnowledgeProposal,
    assertion: KGAssertion,
    version: int,
) -> ProposalResponse:
    cipher = artifact_cipher(request.app.state.settings)
    evidence = []
    for item in await evidence_for_assertion(database, assertion.id, assertion.profile_id):
        try:
            excerpt = cipher.decrypt(item.encrypted_excerpt).decode("utf-8")
        except Exception:
            excerpt = None
        evidence.append(
            EvidenceResponse(
                id=item.id,
                kind=item.kind,
                source_uri=item.source_uri,
                title=item.title,
                excerpt=excerpt,
                observed_at=item.observed_at,
                locator=item.locator,
                artifact_id=item.artifact_id,
            )
        )
    observation = (
        await database.get(LearningObservation, proposal.observation_id)
        if proposal.observation_id
        else None
    )
    return ProposalResponse(
        id=proposal.id,
        assertion=await _assertion_response(database, assertion),
        state=proposal.state,
        base_graph_version=proposal.base_graph_version,
        decision_note=proposal.decision_note,
        current_graph_version=version,
        defer_until=proposal.defer_until,
        decided_by=proposal.decided_by,
        decided_at=proposal.decided_at,
        evidence=evidence,
        observation_state=observation.state if observation else None,
        observation_evidence_count=observation.evidence_count if observation else None,
    )


async def _entity_response(database: Database, entity: KGEntity, version: int) -> EntityResponse:
    assertions = [
        item
        for item in await _assertions_for_entity(database, entity.profile_id, entity.id)
        if item.status == "confirmed"
    ]
    return EntityResponse(
        id=entity.id,
        type=entity.entity_type,
        canonical_name=entity.canonical_name,
        attributes=entity.attributes,
        assertions=[await _assertion_response(database, item) for item in assertions],
        graph_version=version,
    )


async def _entity(database: Database, profile_id: uuid.UUID, entity_id: uuid.UUID) -> KGEntity:
    item = await database.scalar(
        select(KGEntity).where(KGEntity.id == entity_id, KGEntity.profile_id == profile_id)
    )
    if item is None or item.retired_at is not None:
        raise problem(status.HTTP_404_NOT_FOUND, "ENTITY_NOT_FOUND", "Entity not found")
    return item


async def _graph_item(database: Database, object_type: str, object_id: uuid.UUID) -> Any:
    model = {"entity": KGEntity, "relation": KGRelation, "assertion": KGAssertion}.get(
        object_type, KGAssertion
    )
    return await database.get(model, object_id)


@router.get("/knowledge/entities", response_model=EntityPage, tags=["knowledge"])
async def list_entities(
    current: Current,
    database: Database,
    type: str | None = Query(default=None, max_length=32),
    name: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
) -> EntityPage:
    statement = select(KGEntity).where(
        KGEntity.profile_id == current.profile.id, KGEntity.retired_at.is_(None)
    )
    if type:
        try:
            validate_entity_type(type)
        except KnowledgeError as error:
            raise _knowledge_problem(error) from error
        statement = statement.where(KGEntity.entity_type == type)
    if name:
        statement = statement.where(KGEntity.normalized_name.contains(normalize_name(name)))
    items = (await database.scalars(statement.order_by(KGEntity.canonical_name).limit(limit))).all()
    version = await current_graph_version(database, current.profile.id)
    return EntityPage(
        items=[await _entity_response(database, item, version) for item in items],
        has_more=False,
    )


@router.post(
    "/knowledge/entities", response_model=EntityResponse, status_code=201, tags=["knowledge"]
)
async def create_entity(
    values: EntityCreate,
    current: Mutation,
    database: Database,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> EntityResponse:
    try:
        validate_entity_type(values.type)
        expected = (
            await current_graph_version(database, current.profile.id)
            if if_match is None
            else _if_match(if_match)
        )
        item = KGEntity(
            profile_id=current.profile.id,
            entity_type=values.type,
            canonical_name=values.canonical_name.strip(),
            normalized_name=normalize_name(values.canonical_name),
            attributes=values.attributes,
        )
        database.add(item)
        await database.flush()
        version = await commit_graph(
            database,
            profile_id=current.profile.id,
            actor_id=current.user.id,
            actor_type="member",
            expected_version=expected,
            reason="entity_created",
            correlation_id=correlation_id or str(uuid.uuid4()),
            deltas=[GraphDelta("entity", item.id, "create", None, entity_snapshot(item))],
        )
        return await _entity_response(database, item, version)
    except KnowledgeError as error:
        raise _knowledge_problem(error) from error


@router.post(
    "/knowledge/relations", response_model=dict[str, object], status_code=201, tags=["knowledge"]
)
async def create_relation(
    values: RelationCreate,
    current: Mutation,
    database: Database,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> dict[str, object]:
    try:
        validate_relation_type(values.relation_type)
        source = await _entity(database, current.profile.id, values.from_entity_id)
        target = await _entity(database, current.profile.id, values.to_entity_id)
        expected = (
            await current_graph_version(database, current.profile.id)
            if if_match is None
            else _if_match(if_match)
        )
        item = KGRelation(
            profile_id=current.profile.id,
            relation_type=values.relation_type,
            from_entity_id=source.id,
            to_entity_id=target.id,
            attributes=values.attributes,
        )
        database.add(item)
        await database.flush()
        version = await commit_graph(
            database,
            profile_id=current.profile.id,
            actor_id=current.user.id,
            actor_type="member",
            expected_version=expected,
            reason="relation_created",
            correlation_id=correlation_id or str(uuid.uuid4()),
            deltas=[GraphDelta("relation", item.id, "create", None, relation_snapshot(item))],
        )
        return {
            "id": item.id,
            "version": version,
            "from_entity_id": source.id,
            "to_entity_id": target.id,
        }
    except KnowledgeError as error:
        raise _knowledge_problem(error) from error


@router.get("/knowledge/entities/{entity_id}", response_model=EntityResponse, tags=["knowledge"])
async def get_entity(entity_id: uuid.UUID, current: Current, database: Database) -> EntityResponse:
    return await _entity_response(
        database,
        await _entity(database, current.profile.id, entity_id),
        await current_graph_version(database, current.profile.id),
    )


@router.get(
    "/knowledge/entities/{entity_id}/evidence",
    response_model=list[EvidenceResponse],
    tags=["knowledge"],
)
async def get_entity_evidence(
    request: Request, entity_id: uuid.UUID, current: Current, database: Database
) -> list[EvidenceResponse]:
    entity = await _entity(database, current.profile.id, entity_id)
    assertions = [
        item for item in await _assertions_for_entity(database, current.profile.id, entity.id)
    ]
    items: list[EvidenceResponse] = []
    cipher = artifact_cipher(request.app.state.settings)
    for assertion in assertions:
        for item in await evidence_for_assertion(database, assertion.id, current.profile.id):
            try:
                excerpt = cipher.decrypt(item.encrypted_excerpt).decode("utf-8")
            except Exception:
                excerpt = None
            items.append(
                EvidenceResponse(
                    id=item.id,
                    kind=item.kind,
                    source_uri=item.source_uri,
                    title=item.title,
                    excerpt=excerpt,
                    observed_at=item.observed_at,
                    locator=item.locator,
                    artifact_id=item.artifact_id,
                )
            )
    return items


@router.get(
    "/knowledge/entities/{entity_id}/neighbors",
    response_model=list[dict[str, object]],
    tags=["knowledge"],
)
async def neighbors(
    entity_id: uuid.UUID, current: Current, database: Database
) -> list[dict[str, object]]:
    await _entity(database, current.profile.id, entity_id)
    relations = (
        await database.scalars(
            select(KGRelation).where(
                KGRelation.profile_id == current.profile.id,
                KGRelation.retired_at.is_(None),
                or_(KGRelation.from_entity_id == entity_id, KGRelation.to_entity_id == entity_id),
            )
        )
    ).all()
    result = []
    for relation in relations:
        neighbor_id = (
            relation.to_entity_id
            if relation.from_entity_id == entity_id
            else relation.from_entity_id
        )
        neighbor = await _entity(database, current.profile.id, neighbor_id)
        result.append(
            {
                "entity": neighbor.id,
                "name": neighbor.canonical_name,
                "type": neighbor.entity_type,
                "relation": relation.relation_type,
            }
        )
    return result


@router.post(
    "/knowledge/assertions", response_model=AssertionResponse, status_code=201, tags=["knowledge"]
)
async def create_assertion(
    request: Request,
    values: AssertionCreate,
    current: Mutation,
    database: Database,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> AssertionResponse:
    try:
        subject = await _entity(database, current.profile.id, values.subject_entity_id)
        if values.relation_id:
            relation = await database.scalar(
                select(KGRelation).where(
                    KGRelation.id == values.relation_id,
                    KGRelation.profile_id == current.profile.id,
                )
            )
            if relation is None:
                raise KnowledgeError("RELATION_NOT_FOUND", "Relation not found")
        expected = (
            await current_graph_version(database, current.profile.id)
            if if_match is None
            else _if_match(if_match)
        )
        cipher = artifact_cipher(request.app.state.settings)
        item = KGAssertion(
            profile_id=current.profile.id,
            subject_entity_id=subject.id,
            relation_id=values.relation_id,
            predicate=values.predicate,
            value=values.value,
            status="confirmed",
            confidence=values.confidence,
            confidence_method=values.confidence_method,
            valid_from=values.valid_from,
            valid_until=values.valid_until,
            created_by=current.user.id,
        )
        database.add(item)
        await database.flush()
        for evidence_input in values.evidence:
            excerpt = evidence_input.excerpt.encode("utf-8")
            evidence = Evidence(
                profile_id=current.profile.id,
                kind=evidence_input.kind,
                source_uri=evidence_input.source_uri,
                title=evidence_input.title,
                content_hash=hashlib.sha256(excerpt).hexdigest(),
                encrypted_excerpt=cipher.encrypt(excerpt),
                observed_at=evidence_input.observed_at.astimezone(UTC),
                metadata_={},
                locator=evidence_input.locator,
            )
            database.add(evidence)
            await database.flush()
            database.add(
                AssertionEvidence(
                    profile_id=current.profile.id,
                    assertion_id=item.id,
                    evidence_id=evidence.id,
                    support="supports",
                    weight=1.0,
                    locator=evidence_input.locator,
                )
            )
        await commit_graph(
            database,
            profile_id=current.profile.id,
            actor_id=current.user.id,
            actor_type="member",
            expected_version=expected,
            reason="assertion_created",
            correlation_id=correlation_id or str(uuid.uuid4()),
            deltas=[GraphDelta("assertion", item.id, "create", None, assertion_snapshot(item))],
        )
        return await _assertion_response(database, item)
    except KnowledgeError as error:
        raise _knowledge_problem(error) from error


@router.post("/knowledge/search", response_model=list[SearchResult], tags=["knowledge"])
async def search_knowledge(
    values: SearchRequest, current: Current, database: Database
) -> list[SearchResult]:
    query = normalize_name(values.query)
    entities = (
        await database.scalars(
            select(KGEntity).where(
                KGEntity.profile_id == current.profile.id,
                KGEntity.retired_at.is_(None),
            )
        )
    ).all()
    allowed_types = set(values.entity_types)
    allowed_states = set(values.approval_states)
    version = await current_graph_version(database, current.profile.id)
    results = []
    for entity in entities:
        if allowed_types and entity.entity_type not in allowed_types:
            continue
        response = await _entity_response(database, entity, version)
        matched = [
            item
            for item in response.assertions
            if item.status in allowed_states
            and (
                query in normalize_name(entity.canonical_name)
                or query in normalize_name(str(item.value))
            )
        ]
        if query not in normalize_name(entity.canonical_name) and not matched:
            continue
        score = 1.0 if query in normalize_name(entity.canonical_name) else 0.5
        results.append(
            SearchResult(
                entity=response,
                matched_assertion_ids=[item.id for item in matched],
                evidence_ids=[evidence_id for item in matched for evidence_id in item.evidence_ids],
                text_score=score,
                score=score,
            )
        )
    return sorted(results, key=lambda item: (-item.score, item.entity.canonical_name))[
        : values.limit
    ]


@router.post("/knowledge/traverse", response_model=list[GraphPath], tags=["knowledge"])
async def traverse(
    values: TraverseRequest, current: Current, database: Database
) -> list[GraphPath]:
    if any(item not in RELATION_TYPES for item in values.relation_types):
        raise problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "GRAPH_QUERY_OUT_OF_BOUNDS",
            "Relation type is not supported",
        )
    relations = (
        await database.scalars(
            select(KGRelation).where(
                KGRelation.profile_id == current.profile.id,
                KGRelation.retired_at.is_(None),
            )
        )
    ).all()
    allowed = set(values.relation_types)
    adjacency: dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID]]] = defaultdict(list)
    for relation in relations:
        if allowed and relation.relation_type not in allowed:
            continue
        if values.direction in {"out", "both"}:
            adjacency[relation.from_entity_id].append((relation.to_entity_id, relation.id))
        if values.direction in {"in", "both"}:
            adjacency[relation.to_entity_id].append((relation.from_entity_id, relation.id))
    paths: list[GraphPath] = []
    queue: deque[tuple[uuid.UUID, list[uuid.UUID], list[uuid.UUID]]] = deque(
        (start, [start], []) for start in values.start_entity_ids
    )
    while queue and len(paths) < values.max_paths:
        entity_id, entity_path, relation_path = queue.popleft()
        if relation_path:
            paths.append(GraphPath(entities=entity_path, relations=relation_path))
        if len(relation_path) >= values.max_depth:
            continue
        for neighbor, relation_id in adjacency.get(entity_id, []):
            if neighbor in entity_path:
                continue
            queue.append((neighbor, [*entity_path, neighbor], [*relation_path, relation_id]))
    return paths[: values.max_paths]


@router.get("/knowledge/proposals", response_model=list[ProposalResponse], tags=["knowledge"])
async def list_proposals(
    request: Request, current: Current, database: Database
) -> list[ProposalResponse]:
    proposals = (
        await database.scalars(
            select(KnowledgeProposal)
            .where(KnowledgeProposal.profile_id == current.profile.id)
            .order_by(KnowledgeProposal.created_at.desc())
        )
    ).all()
    version = await current_graph_version(database, current.profile.id)
    result = []
    for proposal in proposals:
        assertion = await database.get(KGAssertion, proposal.proposed_assertion_id)
        if assertion:
            result.append(await _proposal_response(request, database, proposal, assertion, version))
    return result


@router.get(
    "/knowledge/proposals/{proposal_id}", response_model=ProposalResponse, tags=["knowledge"]
)
async def get_proposal(
    request: Request, proposal_id: uuid.UUID, current: Current, database: Database
) -> ProposalResponse:
    proposal = await database.scalar(
        select(KnowledgeProposal).where(
            KnowledgeProposal.id == proposal_id, KnowledgeProposal.profile_id == current.profile.id
        )
    )
    if proposal is None:
        raise problem(status.HTTP_404_NOT_FOUND, "PROPOSAL_NOT_FOUND", "Proposal not found")
    assertion = await database.get(KGAssertion, proposal.proposed_assertion_id)
    if assertion is None:
        raise problem(status.HTTP_404_NOT_FOUND, "PROPOSAL_NOT_FOUND", "Proposal not found")
    return await _proposal_response(
        request,
        database,
        proposal,
        assertion,
        await current_graph_version(database, current.profile.id),
    )


@router.post(
    "/knowledge/proposals/{proposal_id}/decision",
    response_model=ProposalResponse,
    tags=["knowledge"],
)
async def decide_proposal(
    proposal_id: uuid.UUID,
    values: ProposalDecision,
    request: Request,
    current: Mutation,
    database: Database,
    if_match: Annotated[str, Header(alias="If-Match")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> ProposalResponse:
    proposal = await database.scalar(
        select(KnowledgeProposal).where(
            KnowledgeProposal.id == proposal_id, KnowledgeProposal.profile_id == current.profile.id
        )
    )
    if proposal is None:
        raise problem(status.HTTP_404_NOT_FOUND, "PROPOSAL_NOT_FOUND", "Proposal not found")
    assertion = await database.get(KGAssertion, proposal.proposed_assertion_id)
    if assertion is None:
        raise problem(status.HTTP_404_NOT_FOUND, "PROPOSAL_NOT_FOUND", "Proposal not found")
    expected = _if_match(if_match)
    if proposal.decision_idempotency_key == idempotency_key and proposal.state not in {
        "pending",
        "deferred",
    }:
        response_assertion = (
            await database.get(KGAssertion, proposal.replacement_assertion_id)
            if proposal.replacement_assertion_id
            else assertion
        )
        return await _proposal_response(
            request,
            database,
            proposal,
            response_assertion or assertion,
            await current_graph_version(database, current.profile.id),
        )
    if proposal.state not in {"pending", "deferred"}:
        raise _knowledge_problem(
            KnowledgeError("PROPOSAL_STATE_INVALID", "Proposal has already been decided")
        )
    current_version = await lock_profile(database, current.profile.id)
    if current_version != expected:
        raise _knowledge_problem(
            KnowledgeError("GRAPH_VERSION_MISMATCH", "Graph changed; reload before saving")
        )
    defer_until = values.defer_until
    correlation = correlation_id or str(uuid.uuid4())
    now = datetime.now(UTC)
    if values.decision == "defer":
        if defer_until is None:
            raise problem(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "DEFER_UNTIL_REQUIRED",
                "defer_until is required when deferring a proposal",
            )
        proposal.state = "deferred"
        proposal.decision_note = values.note
        proposal.defer_until = defer_until
        proposal.decided_by = current.user.id
        proposal.decided_at = now
        proposal.decision_idempotency_key = idempotency_key
        database.add(
            AuditEvent(
                profile_id=current.profile.id,
                actor_id=current.user.id,
                scope="profile",
                action="proposal_deferred",
                target_type="knowledge_proposal",
                target_id=proposal.id,
                correlation_id=correlation,
                metadata_={"defer_until": defer_until.isoformat()},
                occurred_at=now,
            )
        )
        database.add(
            OutboxEvent(
                topic="knowledge.proposal_decided",
                aggregate_type="knowledge_proposal",
                aggregate_id=proposal.id,
                payload={"profile_id": str(current.profile.id), "decision": "defer"},
                occurred_at=now,
            )
        )
        await database.commit()
        await set_profile_context(database, current.profile.id)
    elif values.decision == "reject":
        proposal.state = "rejected"
        proposal.decision_note = values.note
        proposal.decided_by = current.user.id
        proposal.decided_at = now
        proposal.decision_idempotency_key = idempotency_key
        proposal.defer_until = None
        assertion.status = "rejected"
        if proposal.observation_id:
            observation = await database.get(LearningObservation, proposal.observation_id)
            if observation:
                observation.state = "suppressed"
                observation.suppressed_until = None
        database.add(
            AuditEvent(
                profile_id=current.profile.id,
                actor_id=current.user.id,
                scope="profile",
                action="proposal_rejected",
                target_type="knowledge_proposal",
                target_id=proposal.id,
                correlation_id=correlation,
                metadata_={},
                occurred_at=now,
            )
        )
        database.add(
            OutboxEvent(
                topic="knowledge.proposal_decided",
                aggregate_type="knowledge_proposal",
                aggregate_id=proposal.id,
                payload={"profile_id": str(current.profile.id), "decision": "reject"},
                occurred_at=now,
            )
        )
        await database.commit()
        await set_profile_context(database, current.profile.id)
    else:
        if values.decision == "approve_with_edit" and values.value is None:
            raise problem(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "PROPOSAL_VALUE_REQUIRED",
                "value is required when approving with an edit",
            )
        approved_value = (
            values.value
            if values.decision == "approve_with_edit" and values.value is not None
            else assertion.value
        )
        relation_delta = None
        relation_id = assertion.relation_id
        target_id = approved_value.get("entity_id")
        if target_id is not None:
            if not isinstance(target_id, str):
                raise _knowledge_problem(
                    KnowledgeError("RELATION_TARGET_INVALID", "Relation target is invalid")
                )
            try:
                target_uuid = uuid.UUID(target_id)
            except ValueError:
                raise _knowledge_problem(
                    KnowledgeError("RELATION_TARGET_INVALID", "Relation target is invalid")
                ) from None
            try:
                validate_relation_type(assertion.predicate)
            except KnowledgeError as error:
                raise _knowledge_problem(error) from error
            target = await database.scalar(
                select(KGEntity).where(
                    KGEntity.id == target_uuid,
                    KGEntity.profile_id == current.profile.id,
                    KGEntity.retired_at.is_(None),
                )
            )
            if target is None:
                raise _knowledge_problem(
                    KnowledgeError("RELATION_TARGET_NOT_FOUND", "Relation target was not found")
                )
            relation = await database.scalar(
                select(KGRelation).where(
                    KGRelation.profile_id == current.profile.id,
                    KGRelation.relation_type == assertion.predicate,
                    KGRelation.from_entity_id == assertion.subject_entity_id,
                    KGRelation.to_entity_id == target.id,
                    KGRelation.retired_at.is_(None),
                )
            )
            if relation is None:
                relation = KGRelation(
                    profile_id=current.profile.id,
                    relation_type=assertion.predicate,
                    from_entity_id=assertion.subject_entity_id,
                    to_entity_id=target.id,
                    attributes={},
                )
                database.add(relation)
                await database.flush()
                relation_delta = GraphDelta(
                    "relation", relation.id, "create", None, relation_snapshot(relation)
                )
            relation_id = relation.id
        replacement = KGAssertion(
            profile_id=current.profile.id,
            subject_entity_id=assertion.subject_entity_id,
            relation_id=relation_id,
            predicate=assertion.predicate,
            value=approved_value,
            status="confirmed",
            confidence=assertion.confidence,
            confidence_method=assertion.confidence_method,
            valid_from=assertion.valid_from,
            valid_until=assertion.valid_until,
            supersedes_id=assertion.id,
            created_by=current.user.id,
        )
        database.add(replacement)
        await database.flush()
        links = (
            await database.scalars(
                select(AssertionEvidence).where(
                    AssertionEvidence.profile_id == current.profile.id,
                    AssertionEvidence.assertion_id == assertion.id,
                )
            )
        ).all()
        for link in links:
            database.add(
                AssertionEvidence(
                    profile_id=link.profile_id,
                    assertion_id=replacement.id,
                    evidence_id=link.evidence_id,
                    support=link.support,
                    weight=link.weight,
                    locator=link.locator,
                )
            )
        old_snapshot = assertion_snapshot(assertion)
        assertion.status = "superseded"
        proposal.state = "approved"
        proposal.decided_by = current.user.id
        proposal.decided_at = datetime.now(UTC)
        proposal.decision_note = values.note
        proposal.defer_until = None
        proposal.decision_idempotency_key = idempotency_key
        proposal.replacement_assertion_id = replacement.id
        if proposal.observation_id:
            observation = await database.get(LearningObservation, proposal.observation_id)
            if observation:
                observation.state = "confirmed"
                observation.suppressed_until = None
        database.add(
            AuditEvent(
                profile_id=current.profile.id,
                actor_id=current.user.id,
                scope="profile",
                action=f"proposal_{values.decision}",
                target_type="knowledge_proposal",
                target_id=proposal.id,
                correlation_id=correlation,
                metadata_={"graph_version": expected + 1},
                occurred_at=now,
            )
        )
        database.add(
            OutboxEvent(
                topic="knowledge.proposal_decided",
                aggregate_type="knowledge_proposal",
                aggregate_id=proposal.id,
                payload={"profile_id": str(current.profile.id), "decision": values.decision},
                occurred_at=now,
            )
        )
        try:
            await commit_graph(
                database,
                profile_id=current.profile.id,
                actor_id=current.user.id,
                actor_type="member",
                expected_version=expected,
                reason="proposal_approved",
                correlation_id=correlation,
                deltas=[
                    *([relation_delta] if relation_delta else []),
                    GraphDelta(
                        "assertion",
                        assertion.id,
                        "supersede",
                        old_snapshot,
                        assertion_snapshot(assertion),
                    ),
                    GraphDelta(
                        "assertion", replacement.id, "create", None, assertion_snapshot(replacement)
                    ),
                ],
            )
        except KnowledgeError as error:
            raise _knowledge_problem(error) from error
        assertion = replacement
    return await _proposal_response(
        request,
        database,
        proposal,
        assertion,
        await current_graph_version(database, current.profile.id),
    )


@router.get("/knowledge/versions", response_model=list[VersionResponse], tags=["knowledge"])
async def list_versions(current: Current, database: Database) -> list[VersionResponse]:
    items = (
        await database.scalars(
            select(GraphVersion)
            .where(GraphVersion.profile_id == current.profile.id)
            .order_by(GraphVersion.version.desc())
        )
    ).all()
    return [
        VersionResponse(
            version=item.version,
            actor_type=item.actor_type,
            reason=item.reason,
            correlation_id=item.correlation_id,
            created_at=item.created_at,
        )
        for item in items
    ]


@router.get(
    "/knowledge/versions/{version}", response_model=list[ChangeResponse], tags=["knowledge"]
)
async def get_version(version: int, current: Current, database: Database) -> list[ChangeResponse]:
    items = (
        await database.scalars(
            select(GraphChange).where(
                GraphChange.profile_id == current.profile.id, GraphChange.graph_version == version
            )
        )
    ).all()
    return [
        ChangeResponse(
            version=item.graph_version,
            object_type=item.object_type,
            object_id=item.object_id,
            operation=item.operation,
            before=item.before,
            after=item.after,
        )
        for item in items
    ]


@router.get("/knowledge/diff", response_model=list[ChangeResponse], tags=["knowledge"])
async def graph_diff(
    from_version: int, to_version: int, current: Current, database: Database
) -> list[ChangeResponse]:
    if from_version < 0 or to_version < from_version:
        raise problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "INVALID_VERSION_RANGE",
            "Version range is invalid",
        )
    items = (
        await database.scalars(
            select(GraphChange)
            .where(
                GraphChange.profile_id == current.profile.id,
                GraphChange.graph_version > from_version,
                GraphChange.graph_version <= to_version,
            )
            .order_by(GraphChange.graph_version, GraphChange.id)
        )
    ).all()
    return [
        ChangeResponse(
            version=item.graph_version,
            object_type=item.object_type,
            object_id=item.object_id,
            operation=item.operation,
            before=item.before,
            after=item.after,
        )
        for item in items
    ]


@router.post("/knowledge/rollback", response_model=VersionResponse, tags=["knowledge"])
async def rollback(
    values: RollbackRequest,
    current: Mutation,
    database: Database,
    if_match: Annotated[str, Header(alias="If-Match")],
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> VersionResponse:
    expected = _if_match(if_match)
    if values.target_version >= expected:
        raise problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "INVALID_ROLLBACK_TARGET",
            "Rollback target must be older than the current version",
        )
    changes = (
        await database.scalars(
            select(GraphChange)
            .where(
                GraphChange.profile_id == current.profile.id,
                GraphChange.graph_version > values.target_version,
                GraphChange.graph_version <= expected,
            )
            .order_by(GraphChange.graph_version.desc(), GraphChange.id.desc())
        )
    ).all()
    deltas: list[GraphDelta] = []
    for change in changes:
        if change.before is None:
            item = await _graph_item(database, change.object_type, change.object_id)
            if item is None:
                continue
            before = (
                entity_snapshot(item)
                if change.object_type == "entity"
                else relation_snapshot(item)
                if change.object_type == "relation"
                else assertion_snapshot(item)
            )
            if change.object_type in {"entity", "relation"}:
                item.retired_at = datetime.now(UTC)
            else:
                item.status = "retracted"
            after = (
                entity_snapshot(item)
                if change.object_type == "entity"
                else relation_snapshot(item)
                if change.object_type == "relation"
                else assertion_snapshot(item)
            )
        else:
            item = await _graph_item(database, change.object_type, change.object_id)
            if item is None:
                continue
            before = (
                entity_snapshot(item)
                if change.object_type == "entity"
                else relation_snapshot(item)
                if change.object_type == "relation"
                else assertion_snapshot(item)
            )
            await restore_object(database, current.profile.id, change.object_type, change.before)
            item = await _graph_item(database, change.object_type, change.object_id)
            after = (
                entity_snapshot(item)
                if change.object_type == "entity"
                else relation_snapshot(item)
                if change.object_type == "relation"
                else assertion_snapshot(item)
            )
        deltas.append(GraphDelta(change.object_type, change.object_id, "rollback", before, after))
    try:
        version = await commit_graph(
            database,
            profile_id=current.profile.id,
            actor_id=current.user.id,
            actor_type="member",
            expected_version=expected,
            reason=values.reason,
            correlation_id=correlation_id or str(uuid.uuid4()),
            deltas=deltas,
        )
    except KnowledgeError as error:
        raise _knowledge_problem(error) from error
    item = await database.scalar(
        select(GraphVersion).where(
            GraphVersion.profile_id == current.profile.id, GraphVersion.version == version
        )
    )
    assert item is not None
    return VersionResponse(
        version=item.version,
        actor_type=item.actor_type,
        reason=item.reason,
        correlation_id=item.correlation_id,
        created_at=item.created_at,
    )
