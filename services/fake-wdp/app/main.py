from fastapi import FastAPI

app = FastAPI(title="fake-wdp (demo-only WDP stand-in)")


@app.get("/v1/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
