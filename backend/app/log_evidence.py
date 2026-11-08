"""Persistence boundary for processed excerpts; never accepts raw log bytes."""
from typing import Literal

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.log_processing import MAX_OUTPUT_BYTES, MAX_OUTPUT_LINES, LogExcerpt
from app.models import PipelineJobLog


def save_log_excerpt(
    session: Session, job_id: int, excerpt: LogExcerpt,
    *, source_kind: Literal["github", "demo"] = "github", is_curated: bool = False,
) -> None:
    """Replace a job's excerpt inside the caller's transaction.

    An import always passes is_curated=False, revoking prior publication when
    the content changes. The fixture seeder explicitly marks fictional evidence.
    """
    if len(excerpt.lines) > MAX_OUTPUT_LINES or len(excerpt.text.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("Processed excerpt exceeds storage limits.")
    start = excerpt.lines[0].source_line if excerpt.lines else 1
    if any(line.source_line != start + index or "\n" in line.text for index, line in enumerate(excerpt.lines)):
        raise ValueError("Processed excerpt must contain contiguous source lines.")
    row = dict(
        pipeline_job_id=job_id, text=excerpt.text, start_line=start,
        line_count=len(excerpt.lines), total_source_lines=excerpt.total_source_lines,
        truncated=excerpt.truncated, redactions_applied=excerpt.redactions_applied,
        source_kind=source_kind, is_curated=is_curated,
    )
    statement = insert(PipelineJobLog).values(**row)
    session.execute(statement.on_conflict_do_update(
        index_elements=[PipelineJobLog.pipeline_job_id],
        set_={**{key: getattr(statement.excluded, key) for key in row if key != "pipeline_job_id"},
              "updated_at": func.now()},
    ))
