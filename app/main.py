"""SmartZone Air: FastAPI app serving the API and the static accessible frontend."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import llm
from .aqi import AirDataError, fetch_air, geocode
from .planner import Profile, build_plan

logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).resolve().parent.parent / "static"
RATE_LIMIT = 20  # requests per minute per client IP for /api/*
_hits: dict[str, deque[float]] = defaultdict(deque)
CSP = "; ".join([
    "default-src 'self'", "img-src 'self' data:", "style-src 'self'",
    "script-src 'self'", "connect-src 'self'", "frame-ancestors 'none'",
])


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=15, headers={"User-Agent": "SmartZoneAir/1.0"})
    yield
    await app.state.http.aclose()


app = FastAPI(title="SmartZone Air", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def security_and_rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        ip = request.client.host if request.client else "unknown"
        q, now = _hits[ip], time.monotonic()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return JSONResponse({"detail": "Too many requests. Please wait a minute."}, status_code=429)
        q.append(now)
    response = await call_next(request)
    response.headers.update({
        "Content-Security-Policy": CSP,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    })
    return response


class AskRequest(BaseModel):
    profile: Profile
    question: str | None = Field(default=None, max_length=400)


async def _plan(request: Request, profile: Profile) -> dict:
    client: httpx.AsyncClient = request.app.state.http
    try:
        loc = await geocode(client, profile.city.strip())
        snap = await fetch_air(client, loc)
    except AirDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Air-quality service is unavailable. Try again shortly.") from exc
    return build_plan(snap, profile)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "ai_provider": llm.provider() or "rules"}


@app.post("/api/plan")
async def plan(request: Request, body: AskRequest) -> dict:
    result = await _plan(request, body.profile)
    text, source = await llm.summarise(request.app.state.http, result, body.question)
    result["ai"] = {"text": text, "source": source}
    return result


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
