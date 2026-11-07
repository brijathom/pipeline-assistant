from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PipelineRun
from app.seed import seed_runs


def test_seed_is_idempotent(db_session: Session) -> None:
    assert seed_runs(db_session) == 3
    db_session.commit()
    assert seed_runs(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(PipelineRun)) == 3
