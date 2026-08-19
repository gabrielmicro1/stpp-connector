from fastapi import FastAPI

app = FastAPI(title="PSP7 Integration API")


@app.get("/v1/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
