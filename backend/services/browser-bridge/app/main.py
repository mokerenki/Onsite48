from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Onsite48 Browser Bridge Service", version="1.0.0")


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "onsite48-browser-bridge"


@app.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "onsite48-browser-bridge", "status": "running"}
