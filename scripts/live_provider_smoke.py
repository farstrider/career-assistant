from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic import SecretStr

from career_assistant.reasoning import (
    JobEnrichmentData,
    OpenAICompatibleGateway,
    ReasoningRequest,
    validate_enrichment,
)
from career_assistant.settings import LLMSettings


async def main() -> None:
    endpoint = os.environ.get("CAREER_LLM_ENDPOINT")
    model = os.environ.get("CAREER_LLM_MODEL")
    if not endpoint or not model:
        raise SystemExit("set CAREER_LLM_ENDPOINT and CAREER_LLM_MODEL for the live smoke")
    settings = LLMSettings(
        endpoint=endpoint,
        model=model,
        api_key=(
            SecretStr(os.environ["CAREER_LLM_API_KEY"])
            if os.environ.get("CAREER_LLM_API_KEY")
            else None
        ),
        max_tokens=256,
        task_token_budget=512,
        daily_token_budget=512,
    )
    evidence = {
        "job.title": "Senior Platform Engineering Manager",
        "job.description": "Lead a senior platform engineering team in Tokyo.",
        "job.skills": "Python\nPostgreSQL",
    }
    template = Path(__file__).parents[1].joinpath("prompts/job-enrichment-v1.txt").read_text()
    prompt = template.replace(
        "{{evidence}}",
        "\n".join(f"[{key}] {value}" for key, value in evidence.items()),
    )
    gateway = OpenAICompatibleGateway(settings)
    try:
        result = await gateway.run(
            ReasoningRequest(
                task="job_enrichment",
                messages=[{"role": "user", "content": prompt}],
                schema=JobEnrichmentData.model_json_schema(),
                max_tokens=settings.max_tokens,
                timeout_seconds=settings.timeout_seconds,
                idempotency_key="live-provider-smoke-v1",
            )
        )
        validate_enrichment(result.data, evidence)
        print({"provider": result.provider, "model": result.model, "usage": result.usage})
    finally:
        await gateway.close()


if __name__ == "__main__":
    asyncio.run(main())
