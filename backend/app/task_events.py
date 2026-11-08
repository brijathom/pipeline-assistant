"""Bounded SSE snapshots; no open database transaction between status checks."""
import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from threading import BoundedSemaphore

from fastapi import HTTPException, Request
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from app.database import get_engine
from app.schemas import InvestigationTaskResponse
from app.task_queries import list_task_summaries

POLL_SECONDS = 2
HEARTBEAT_SECONDS = 10
STREAM_SECONDS = 45
MAX_FRAME_BYTES = 128 * 1024
stream_slots = BoundedSemaphore(4)  # Per API process, after reviewer authentication.
task_list = TypeAdapter(list[InvestigationTaskResponse])


def read_task_snapshot() -> list[dict]:
    with Session(get_engine()) as session:
        session.execute(text("SET LOCAL statement_timeout = '3000ms'"))
        return list_task_summaries(session)


def get_task_snapshot_reader() -> Callable[[], list[dict]]:
    # Returning a function does not open a connection during dependency resolution.
    return read_task_snapshot


def snapshot_frame(rows: list[dict]) -> str:
    if len(rows) > 20:
        raise ValueError("Too many tasks in the stream snapshot.")
    tasks = task_list.validate_python(rows)
    data = json.dumps([task.model_dump(mode="json") for task in tasks], separators=(",", ":"), ensure_ascii=False)
    version = hashlib.sha256(data.encode("utf-8")).hexdigest()
    frame = f"id: {version}\nevent: tasks\ndata: {data}\n\n"
    if len(frame.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError("Task snapshot exceeds the stream limit.")
    return frame


async def task_events(request: Request, read: Callable, initial: str):
    started = last_sent = time.monotonic()
    previous = initial
    yield initial  # Always resynchronize, even when a client sends Last-Event-ID.
    while time.monotonic() - started < STREAM_SECONDS:
        await asyncio.sleep(min(POLL_SECONDS, max(0, STREAM_SECONDS - (time.monotonic() - started))))
        if await request.is_disconnected():
            return
        if time.monotonic() - started >= STREAM_SECONDS:
            break
        try:
            current = snapshot_frame(await run_in_threadpool(read))
        except (SQLAlchemyError, ValidationError, ValueError):
            yield "event: stream-error\ndata: {}\n\n"
            return
        if current != previous:
            yield current
            previous, last_sent = current, time.monotonic()
        elif time.monotonic() - last_sent >= HEARTBEAT_SECONDS:
            yield ": keep-alive\n\n"
            last_sent = time.monotonic()
    yield "event: rotate\ndata: {}\n\n"


class TaskEventResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Includes cancellation before the body iterator starts and send failures.
            stream_slots.release()


async def open_task_stream(request: Request, read: Callable) -> StreamingResponse:
    if not stream_slots.acquire(blocking=False):
        raise HTTPException(429, "Too many task streams. Close another task view or retry shortly.", headers={"Retry-After": "5"})
    try:
        initial = snapshot_frame(await run_in_threadpool(read))
        return TaskEventResponse(task_events(request, read, initial), media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff",
        })
    except (SQLAlchemyError, ValidationError, ValueError):
        stream_slots.release()
        raise HTTPException(503, "Task progress is temporarily unavailable.") from None
    except BaseException:
        stream_slots.release()
        raise
