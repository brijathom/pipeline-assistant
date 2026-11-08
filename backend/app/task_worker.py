"""Execute one durable task locally; a future Pub/Sub consumer uses this boundary."""
import argparse
import asyncio
import sys
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_engine
from app.investigation_tasks import TaskError, execute_task, expire_interrupted_task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", type=UUID, required=True)
    parser.add_argument("--mark-interrupted", action="store_true", help="Fail a running task older than 120 seconds, without retrying its model request")
    args = parser.parse_args()
    try:
        engine = get_engine()
        if args.mark_interrupted:
            with Session(engine) as session:
                expire_interrupted_task(session, args.task_id)
                session.commit()
            status = "failed (worker_interrupted)"
        else:
            status = asyncio.run(execute_task(args.task_id, engine, api_key=get_settings().gemini_api_key))
    except TaskError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (ValidationError, SQLAlchemyError):
        print("Worker configuration or database operation failed. Check task status before taking further action.", file=sys.stderr)
        return 1
    print(f"Task {args.task_id}: {status}. No report was approved or published.")
    return 1 if status.startswith("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
