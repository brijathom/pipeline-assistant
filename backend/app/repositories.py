import re
from typing import Annotated

from pydantic import AfterValidator


def normalize_repository(value: str) -> str:
    """Accept an owner/repository name, never a URL or additional path."""
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,38}/[a-z0-9_.-]{1,100}", value):
        raise ValueError("Repository must be a GitHub owner/repository name")
    if value.split("/")[1] in {".", ".."}:
        raise ValueError("Repository must contain a repository name")
    return value


RepositoryName = Annotated[str, AfterValidator(normalize_repository)]
