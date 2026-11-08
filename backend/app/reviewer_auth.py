"""Single-operator bearer authentication for private reviewer access."""
from dataclasses import dataclass
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

bearer = HTTPBearer(auto_error=False, scheme_name="ReviewerToken")
PRIVATE_HEADERS = {"Cache-Control": "no-store"}


@dataclass(frozen=True)
class ReviewerPrincipal:
    name: str


def require_reviewer(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> ReviewerPrincipal:
    expected = settings.reviewer_access_token
    if expected is None:
        raise HTTPException(503, "Reviewer access is not configured.", headers=PRIVATE_HEADERS)
    if credentials is None or not compare_digest(
        credentials.credentials.encode("utf-8"), expected.get_secret_value().encode("utf-8"),
    ):
        raise HTTPException(401, "Reviewer authentication required.", headers={**PRIVATE_HEADERS, "WWW-Authenticate": "Bearer"})
    return ReviewerPrincipal(name="operator")
