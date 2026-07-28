import asyncio

from celery import Celery  # type: ignore[import-untyped]

from career_assistant.auth import cleanup_sessions
from career_assistant.services import Services
from career_assistant.settings import load_settings

settings = load_settings()
celery = Celery(
    "career_assistant",
    broker=settings.redis.url.get_secret_value(),  # type: ignore[union-attr]
)
celery.conf.update(
    task_default_queue=settings.redis.task_queue,
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    beat_schedule={
        "cleanup-auth-sessions": {
            "task": "career_assistant.cleanup_auth_sessions",
            "schedule": 24 * 60 * 60,
        }
    },
)


@celery.task(name="career_assistant.healthcheck")  # type: ignore[untyped-decorator]
def healthcheck() -> str:
    return "ok"


@celery.task(name="career_assistant.cleanup_auth_sessions")  # type: ignore[untyped-decorator]
def cleanup_auth_sessions() -> int:
    async def run() -> int:
        services = Services.create(settings)
        try:
            async with services.sessions() as database:
                return await cleanup_sessions(database)
        finally:
            await services.close()

    return asyncio.run(run())
