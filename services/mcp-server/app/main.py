from fastapi import FastAPI

app = FastAPI(title="PSP7 MCP Server")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
