from fastapi import FastAPI, Request

from app.metrics import router as metrics_router
from app.runs import router as runs_router
from app.reviewer import router as reviewer_router

app = FastAPI(title="Pipeline Assistant")
app.include_router(runs_router)
app.include_router(metrics_router)
app.include_router(reviewer_router)


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
