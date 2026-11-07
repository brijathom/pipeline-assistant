from fastapi import FastAPI

from app.metrics import router as metrics_router
from app.runs import router as runs_router

app = FastAPI(title="Pipeline Assistant")
app.include_router(runs_router)
app.include_router(metrics_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
