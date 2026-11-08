from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.metrics import router as metrics_router
from app.runs import router as runs_router
from app.reviewer import router as reviewer_router

app = FastAPI(title="Pipeline Assistant")
app.include_router(runs_router)
app.include_router(metrics_router)
app.include_router(reviewer_router)


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, error: RequestValidationError):
    if request.url.path.startswith("/api/reviewer/"):
        # FastAPI's default validation payload echoes submitted values, which
        # can include private review notes. Return only a safe generic message.
        return JSONResponse(status_code=422, content={"detail": "Invalid reviewer request."})
    return await request_validation_exception_handler(request, error)


@app.middleware("http")
async def private_response_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/api/reviewer" or request.url.path.startswith("/api/reviewer/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers.add_vary_header("Authorization")
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
