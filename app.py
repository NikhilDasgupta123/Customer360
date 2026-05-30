from fastapi import FastAPI

from customergraph.api.routes import router as day1_router

app = FastAPI(
    title="CustomerGraph AI",
    version="0.1.0",
    description="CustomerGraph AI dev",
)

app.include_router(day1_router, prefix="/api/v1")


@app.get("/", tags=["Root"])
def root() -> dict:
    return {
        "ok": True,
        "message": "CustomerGraph AI Day 1 backend is running",
        "docs": "http://127.0.0.1:8000/docs",
    }


@app.get("/health", tags=["Health"])
def health() -> dict:
    return {
        "ok": True,
        "service": "customergraph-ai-day1-backend",
        "status": "healthy",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)