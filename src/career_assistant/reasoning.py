from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from career_assistant.models import JobEnrichment, JobVersion, PromptTemplate, ReasoningRun
from career_assistant.settings import LLMSettings, Settings

PROMPT_KEY = "job_enrichment"
PROMPT_VERSION = "1.0.0"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "job-enrichment-v1.txt"
_SUSPICIOUS_OUTPUT = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous|system\s+message|developer\s+message|reveal\s+the\s+prompt)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{7,}\d)(?!\w)")
_SECRET = re.compile(r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*[^\s,;]+")


class EnrichmentItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class EnrichmentDimension(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: Literal[
        "unknown", "entry", "mid", "senior", "staff", "principal", "director", "executive"
    ]
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_evidence_for_known_values(self) -> EnrichmentDimension:
        if self.label == "unknown" and self.evidence_ids:
            raise ValueError("unknown dimensions cannot cite evidence")
        if self.label != "unknown" and not self.evidence_ids:
            raise ValueError("known dimensions require evidence")
        return self


class Compensation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["unknown", "posted", "estimated"]
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    lower: float | None = Field(default=None, ge=0)
    upper: float | None = Field(default=None, ge=0)
    period: Literal["hour", "month", "year"] | None = None
    method: str | None = Field(default=None, max_length=200)
    market: str | None = Field(default=None, max_length=200)
    as_of: str | None = Field(default=None, max_length=32)
    confidence: float = Field(default=0, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_status(self) -> Compensation:
        if self.status == "unknown":
            if (
                any(
                    value is not None
                    for value in (
                        self.currency,
                        self.lower,
                        self.upper,
                        self.period,
                        self.method,
                        self.market,
                        self.as_of,
                    )
                )
                or self.evidence_ids
                or self.confidence != 0
            ):
                raise ValueError("unknown compensation cannot contain an estimate or citations")
            return self
        if not self.evidence_ids or not self.currency or not self.period:
            raise ValueError("known compensation requires currency, period, and evidence")
        if self.lower is None and self.upper is None:
            raise ValueError("known compensation requires a lower or upper bound")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("compensation lower bound cannot exceed upper bound")
        if self.status == "estimated" and not self.method:
            raise ValueError("estimated compensation requires a method")
        return self


class JobEnrichmentData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: EnrichmentItem
    responsibilities: list[EnrichmentItem] = Field(max_length=30)
    technologies: list[EnrichmentItem] = Field(max_length=50)
    seniority: EnrichmentDimension
    leadership: EnrichmentDimension
    culture_signals: list[EnrichmentItem] = Field(max_length=30)
    compensation: Compensation
    uncertainties: list[str] = Field(max_length=30)


class ReasoningError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReasoningRequest:
    task: str
    messages: list[dict[str, str]]
    schema: dict[str, object]
    max_tokens: int
    timeout_seconds: int
    idempotency_key: str


@dataclass(frozen=True)
class ReasoningResult:
    data: dict[str, object]
    provider: str
    model: str
    usage: dict[str, object]
    request_id: str | None
    raw_output: str
    latency_ms: int


class ReasoningGateway(Protocol):
    async def run(self, request: ReasoningRequest) -> ReasoningResult: ...


class OpenAICompatibleGateway:
    provider = "openai_compatible"

    def __init__(self, settings: LLMSettings, client: httpx.AsyncClient | None = None) -> None:
        if settings.endpoint is None or settings.model is None:
            raise ReasoningError("LLM_NOT_CONFIGURED", "LLM provider is not configured")
        self.settings = settings
        self.client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def run(self, request: ReasoningRequest) -> ReasoningResult:
        assert self.settings.endpoint is not None
        assert self.settings.model is not None
        payload = {
            "model": self.settings.model,
            "messages": request.messages,
            "temperature": 0,
            "max_tokens": request.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.task,
                    "strict": True,
                    "schema": request.schema,
                },
            },
        }
        headers = {"Idempotency-Key": request.idempotency_key}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key.get_secret_value()}"
        started = time.monotonic()
        for attempt in range(2):
            try:
                response = await self.client.post(
                    self.settings.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=request.timeout_seconds,
                )
                if response.status_code in {408, 429} or response.status_code >= 500:
                    if attempt == 0:
                        continue
                    raise ReasoningError("LLM_PROVIDER_UNAVAILABLE", "Provider request failed")
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise ReasoningError("LLM_INVALID_RESPONSE", "Provider returned no JSON text")
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise ReasoningError("LLM_INVALID_RESPONSE", "Provider returned a JSON value")
                usage = body.get("usage", {})
                if not isinstance(usage, dict):
                    usage = {}
                return ReasoningResult(
                    data=data,
                    provider=self.provider,
                    model=self.settings.model,
                    usage={
                        key: value
                        for key, value in usage.items()
                        if isinstance(value, (int, float))
                    },
                    request_id=body.get("id") if isinstance(body.get("id"), str) else None,
                    raw_output=content,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            except (httpx.TimeoutException, httpx.TransportError) as error:
                if attempt == 0:
                    continue
                raise ReasoningError("LLM_TIMEOUT", "Provider request timed out") from error
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                raise ReasoningError(
                    "LLM_INVALID_RESPONSE", "Provider response was invalid"
                ) from error
            except httpx.HTTPStatusError as error:
                raise ReasoningError("LLM_PROVIDER_FAILED", "Provider request failed") from error
        raise ReasoningError("LLM_PROVIDER_UNAVAILABLE", "Provider request failed")


def redact_text(value: str) -> str:
    value = _EMAIL.sub("[REDACTED_EMAIL]", value)
    value = _PHONE.sub("[REDACTED_PHONE]", value)
    return _SECRET.sub("[REDACTED_SECRET]", value)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prompt_definition() -> tuple[str, dict[str, object], dict[str, object], str]:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    schema = JobEnrichmentData.model_json_schema()
    input_schema = {
        "type": "object",
        "properties": {"evidence": {"type": "string"}},
        "required": ["evidence"],
        "additionalProperties": False,
    }
    content_hash = _sha256(
        json.dumps(
            {"template": template, "input_schema": input_schema, "output_schema": schema},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return template, input_schema, schema, content_hash


async def ensure_prompt_template(database: AsyncSession) -> PromptTemplate:
    template, input_schema, output_schema, content_hash = prompt_definition()
    prompt = await database.scalar(
        select(PromptTemplate).where(
            PromptTemplate.key == PROMPT_KEY,
            PromptTemplate.version == PROMPT_VERSION,
        )
    )
    if prompt is not None:
        if prompt.content_hash != content_hash:
            raise ReasoningError(
                "PROMPT_HASH_MISMATCH", "Stored prompt differs from application prompt"
            )
        return prompt
    prompt = PromptTemplate(
        key=PROMPT_KEY,
        version=PROMPT_VERSION,
        task="job_enrichment",
        template=template,
        input_schema=input_schema,
        output_schema=output_schema,
        status="active",
        content_hash=content_hash,
    )
    database.add(prompt)
    await database.flush()
    return prompt


def evidence_for_job(version: JobVersion) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for key, value in version.normalized_data.items():
        text = "\n".join(str(item) for item in value) if isinstance(value, list) else str(value)
        if text.strip():
            evidence[f"job.{key}"] = redact_text(text)
    return evidence


def build_request(
    version: JobVersion, prompt: PromptTemplate, settings: LLMSettings
) -> tuple[ReasoningRequest, str]:
    evidence = evidence_for_job(version)
    evidence_text = "\n".join(f"[{key}] {value}" for key, value in sorted(evidence.items()))
    rendered = prompt.template.replace("{{evidence}}", evidence_text)
    input_hash = _sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return (
        ReasoningRequest(
            task=prompt.task,
            messages=[{"role": "user", "content": rendered}],
            schema=prompt.output_schema,
            max_tokens=settings.max_tokens,
            timeout_seconds=settings.timeout_seconds,
            idempotency_key=f"job-enrichment:{version.id}:{prompt.version}",
        ),
        input_hash,
    )


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[\w+#.-]+", value.casefold()) if len(token) >= 4}


def _check_claim(value: str, evidence_ids: list[str], evidence: dict[str, str]) -> None:
    if not evidence_ids:
        raise ValueError("claim requires evidence")
    if not any(_tokens(value) & _tokens(evidence[item]) for item in evidence_ids):
        raise ValueError("claim is not grounded in cited evidence")


def validate_enrichment(raw: dict[str, object], evidence: dict[str, str]) -> JobEnrichmentData:
    try:
        data = JobEnrichmentData.model_validate(raw)
    except ValidationError as error:
        raise ReasoningError(
            "LLM_SCHEMA_INVALID", "Provider output did not match the schema"
        ) from error
    all_ids = {
        evidence_id for claim in data.model_dump().values() for evidence_id in _evidence_ids(claim)
    }
    if all_ids - evidence.keys():
        raise ReasoningError("LLM_EVIDENCE_INVALID", "Provider cited unavailable evidence")
    try:
        if _SUSPICIOUS_OUTPUT.search(data.summary.value):
            raise ValueError("suspicious model output")
        _check_claim(data.summary.value, data.summary.evidence_ids, evidence)
        for item in (*data.responsibilities, *data.technologies, *data.culture_signals):
            if _SUSPICIOUS_OUTPUT.search(item.value):
                raise ValueError("suspicious model output")
            _check_claim(item.value, item.evidence_ids, evidence)
        for dimension in (data.seniority, data.leadership):
            if dimension.label != "unknown":
                _check_claim(dimension.label, dimension.evidence_ids, evidence)
    except ValueError as error:
        raise ReasoningError("LLM_GROUNDING_INVALID", "Provider output was not grounded") from error
    return data


def _evidence_ids(value: object) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            if key == "evidence_ids" and isinstance(item, list):
                result.extend(item for item in item if isinstance(item, str))
            else:
                result.extend(_evidence_ids(item))
        return result
    if isinstance(value, list):
        return [item for child in value for item in _evidence_ids(child)]
    return []


def _errors(error: Exception) -> list[dict[str, str]]:
    return [{"code": getattr(error, "code", "LLM_FAILED"), "detail": str(error)[:300]}]


async def _check_daily_budget(database: AsyncSession, settings: LLMSettings, task: str) -> None:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    usages = (
        await database.scalars(
            select(ReasoningRun.usage).where(
                ReasoningRun.task == task,
                ReasoningRun.state == "completed",
                ReasoningRun.created_at >= start,
            )
        )
    ).all()
    used = 0
    for usage in usages:
        total = usage.get("total_tokens")
        if isinstance(total, (int, float)):
            used += int(total)
        else:
            token_values = [
                usage.get(key)
                for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens")
            ]
            used += sum(int(value) for value in token_values if isinstance(value, (int, float)))
    if used + settings.max_tokens > settings.daily_token_budget:
        raise ReasoningError("LLM_BUDGET_EXCEEDED", "Configured daily LLM budget is exhausted")


async def enrich_job_version(
    database: AsyncSession,
    settings: Settings,
    job_version: JobVersion,
    gateway: ReasoningGateway | None = None,
) -> JobEnrichment | None:
    existing = await database.scalar(
        select(JobEnrichment).where(JobEnrichment.job_version_id == job_version.id)
    )
    if existing is not None:
        return existing
    prompt = await ensure_prompt_template(database)
    request, input_hash = build_request(job_version, prompt, settings.llm)
    existing_run = await database.scalar(
        select(ReasoningRun).where(ReasoningRun.idempotency_key == request.idempotency_key)
    )
    if existing_run is not None:
        return None
    run = ReasoningRun(
        profile_id=None,
        job_version_id=job_version.id,
        prompt_template_id=prompt.id,
        task=request.task,
        provider=settings.llm.provider if settings.llm.endpoint else "none",
        model=settings.llm.model or "none",
        schema_hash=_sha256(
            json.dumps(prompt.output_schema, sort_keys=True, separators=(",", ":"))
        ),
        input_hash=input_hash,
        usage={},
        state="running",
        validation_errors=[],
        idempotency_key=request.idempotency_key,
    )
    database.add(run)
    await database.commit()
    if settings.llm.endpoint is None:
        run.state = "skipped"
        run.validation_errors = [{"code": "LLM_NOT_CONFIGURED", "detail": "Provider is disabled"}]
        run.completed_at = datetime.now(UTC)
        await database.commit()
        return None
    owned_gateway = gateway is None
    provider = gateway or OpenAICompatibleGateway(settings.llm)
    try:
        await _check_daily_budget(database, settings.llm, request.task)
        result = await provider.run(request)
        run.provider = result.provider
        run.model = result.model
        run.request_id = result.request_id
        run.usage = result.usage
        run.latency_ms = result.latency_ms
        run.output_hash = _sha256(result.raw_output)
        data = validate_enrichment(result.data, evidence_for_job(job_version))
        enrichment = JobEnrichment(
            job_version_id=job_version.id,
            reasoning_run_id=run.id,
            data=data.model_dump(mode="json"),
        )
        database.add(enrichment)
        run.state = "completed"
        run.completed_at = datetime.now(UTC)
        await database.commit()
        return enrichment
    except ReasoningError as error:
        run.state = (
            "quarantined"
            if error.code in {"LLM_SCHEMA_INVALID", "LLM_EVIDENCE_INVALID", "LLM_GROUNDING_INVALID"}
            else "failed"
        )
        run.validation_errors = cast(list[object], _errors(error))
        run.completed_at = datetime.now(UTC)
        await database.commit()
        return None
    finally:
        if owned_gateway and isinstance(provider, OpenAICompatibleGateway):
            await provider.close()
