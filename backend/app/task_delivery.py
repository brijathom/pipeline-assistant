"""Database-backed dispatch; external message data contains only a task UUID."""
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

from sqlalchemy import Engine, func, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.investigation_tasks import TaskError, execute_task
from app.models import InvestigationTask

MAX_DISPATCH_ATTEMPTS = 5


def dispatch_one(bind: Engine | Connection, publish: Callable[[bytes], None]) -> tuple[UUID, bool] | None:
    # Commit the attempt/lease BEFORE network I/O. A crash consumes an attempt,
    # but leaves the row eligible after the delay; no transaction spans the RPC.
    with Session(bind, join_transaction_mode="create_savepoint") as session:
        task = session.scalar(select(InvestigationTask).where(
            InvestigationTask.status == "queued", InvestigationTask.dispatched_at.is_(None),
            InvestigationTask.dispatch_attempts < MAX_DISPATCH_ATTEMPTS,
            InvestigationTask.dispatch_after <= func.clock_timestamp(),
        ).order_by(InvestigationTask.dispatch_after, InvestigationTask.id).with_for_update(skip_locked=True).limit(1))
        if task is None:
            return None
        task_id = task.id
        task.dispatch_attempts += 1
        task.dispatch_after = session.scalar(select(func.clock_timestamp())) + timedelta(seconds=30 * 2 ** (task.dispatch_attempts - 1))
        session.commit()
    try:
        publish(str(task_id).encode("ascii"))
    except Exception:
        # Ambiguous publish failures may have delivered. A later attempt can
        # duplicate the message, which the committed worker claim makes safe.
        return task_id, False
    with Session(bind, join_transaction_mode="create_savepoint") as session:
        session.execute(update(InvestigationTask).where(InvestigationTask.id == task_id).values(dispatched_at=func.clock_timestamp()))
        session.commit()
    return task_id, True


async def handle_message(data: bytes, bind: Engine | Connection, *, api_key=None, generate=None) -> str:
    # Reject oversized/malformed payloads without looking up evidence or calling AI.
    if len(data) != 36:
        return "discarded"
    try:
        task_id = UUID(data.decode("ascii"))
        if str(task_id).encode("ascii") != data:
            return "discarded"
    except (ValueError, UnicodeError):
        return "discarded"
    try:
        return await execute_task(task_id, bind, api_key=api_key, generate=generate)
    except TaskError as error:
        if error.status_code == 404:
            return "missing"  # Deleted/unknown tasks are poison messages, not retryable work.
        raise
