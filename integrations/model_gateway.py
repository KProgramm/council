"""Small authenticated Ollama gateway for teammates; no model administration API."""
import asyncio
import hmac
import json
import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

MAX_BODY_BYTES = 256_000
MAX_INFLIGHT = 16


def create_gateway(token: str, upstream: str = "http://127.0.0.1:11434",
                   model: str = "llama3.1:8b", transport=None) -> FastAPI:
    if len(token) < 32:
        raise ValueError("Configure a model gateway access key of at least 32 characters")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    slot = asyncio.Semaphore(1)
    inflight = 0

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        auth = request.headers.get("authorization", "")
        scheme, _, candidate = auth.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(candidate.encode(), token.encode()):
            return JSONResponse({"error": "A valid model access key is required."}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy(path: str, request: Request):
        nonlocal inflight
        reads = {"api/tags", "api/version"}
        calls = {"api/generate", "api/chat", "api/show"}
        if not ((request.method == "GET" and path in reads) or (request.method == "POST" and path in calls)):
            raise HTTPException(404, "This endpoint is not available.")
        body = None
        if request.method == "POST":
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_BODY_BYTES:
                    raise HTTPException(413, "The model request is too large.")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                raise HTTPException(422, "Send a JSON model request.") from None
            if not isinstance(body, dict) or body.get("model") != model:
                raise HTTPException(422, "Use the configured Council model.")
            if path != "api/show":
                if body.get("stream"):
                    raise HTTPException(422, "This shared endpoint supports non-streaming model requests.")
                body["stream"] = False
                options = body.get("options", {})
                if not isinstance(options, dict):
                    raise HTTPException(422, "Model options must be an object.")
                # Keep one model resident with bounded context/output on the shared T4.
                body["options"] = {**options, "num_ctx": 16384, "num_predict": 2048}
                body["keep_alive"] = "30m"
        if inflight >= MAX_INFLIGHT:
            raise HTTPException(429, "The shared model is busy. Try again shortly.")
        inflight += 1
        try:
            async with slot:
                async with httpx.AsyncClient(timeout=300, transport=transport, follow_redirects=False) as client:
                    response = await client.request(request.method, f"{upstream.rstrip('/')}/{path}", json=body)
            if response.status_code != 200:
                raise HTTPException(502, "The Brev model could not complete this request.")
            return Response(response.content, media_type="application/json")
        except httpx.TimeoutException:
            raise HTTPException(504, "The Brev model took too long. Try again.") from None
        except httpx.HTTPError:
            raise HTTPException(502, "The Brev model is unavailable. Try again.") from None
        finally:
            inflight -= 1

    return app


def from_environment() -> FastAPI:
    return create_gateway(os.getenv("MODEL_GATEWAY_TOKEN", ""),
                          os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                          os.getenv("OLLAMA_MODEL", "llama3.1:8b"))
