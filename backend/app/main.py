from fastapi import FastAPI

from app.runs import router as runs_router

app = FastAPI(title="Pipeline Assistant")
app.include_router(runs_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
