from fastapi import FastAPI

app = FastAPI(title="Atlas Research")


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Used by Docker and deployment platforms to check the API is alive."""
    return {"status": "ok"}
