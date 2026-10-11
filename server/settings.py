"""Local connection settings. Secrets are accepted on save, never returned."""
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlsplit, urlunsplit

from dotenv import set_key
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
import httpx

from server.security import LOCAL_HOSTS, local_only
from integrations.model_config import TEAM_MODEL_URL, KEY_NAMES, MODEL_NAMES, provider_key, provider_config

class ConnectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["brev", "cisco", "paid"]
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=2048)
    max_concurrent_llm_calls: int = Field(ge=1, le=16)
    github_token: SecretStr | None = Field(default=None, max_length=4096)
    llm_api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_github_token: bool = False
    clear_llm_api_key: bool = False

    @field_validator("model", "base_url")
    @classmethod
    def single_line(cls, value: str) -> str:
        value = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Use a single line")
        return value

    @field_validator("github_token", "llm_api_key")
    @classmethod
    def single_line_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in raw):
            raise ValueError("Use a single line")
        return SecretStr(raw)


def public_url(value: str) -> str:
    """Never echo credentials embedded in an existing environment URL."""
    try:
        parts = urlsplit(value)
        if not parts.hostname or parts.scheme not in {"http", "https"}:
            return ""
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except ValueError:
        return ""


def current_settings() -> dict:
    provider = os.getenv("LLM_PROVIDER", "brev").lower()
    try:
        concurrency = max(1, min(16, int(os.getenv("MAX_CONCURRENT_LLM_CALLS", "3"))))
    except ValueError:
        concurrency = 3
    return {
        "provider": provider,
        "providers": {name: {**provider_config(name), "base_url": public_url(provider_config(name)["base_url"])} for name in KEY_NAMES},
        "max_concurrent_llm_calls": concurrency,
        "github_token_configured": bool(os.getenv("GITHUB_TOKEN", "").strip()),
        "llm_api_key_configured": bool(provider_key(provider)) if provider in KEY_NAMES else False,
        "provider_keys_configured": {name: bool(provider_key(name)) for name in KEY_NAMES},
        "team_model_url": TEAM_MODEL_URL,
    }


def persist_environment(path: Path, updates: dict[str, str]) -> None:
    if path.is_symlink():
        raise HTTPException(409, "The local .env is a symbolic link. Configure it directly instead.")
    temp_path = None
    try:
        # Write next to .env so replacement is atomic; preserve unrelated settings/comments.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            if path.exists():
                temp.write(path.read_text(encoding="utf-8"))
        for key, value in updates.items():
            set_key(str(temp_path), key, value, quote_mode="always")
        os.chmod(temp_path, 0o600)
        with temp_path.open("rb") as temp:
            os.fsync(temp.fileno())
        os.replace(temp_path, path)
    except (OSError, UnicodeError):
        raise HTTPException(500, "Could not save the local .env. Your running settings were not changed.") from None
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    os.environ.update(updates)


def create_settings_router(env_path: Path, reviews_active: Callable[[], bool]) -> APIRouter:
    router = APIRouter(prefix="/settings", tags=["Settings"], dependencies=[Depends(local_only)])

    @router.get("")
    async def read_settings():
        return current_settings()

    @router.post("")
    async def save_settings(request: Request):
        try:
            settings = ConnectionSettings.model_validate(await request.json())
        except (ValidationError, json.JSONDecodeError, UnicodeError):
            # Pydantic's default errors echo input values, which may contain a key.
            raise HTTPException(422, "Check the provider, model, URL, and concurrency (1–16). Keys must use a single line.") from None
        if reviews_active():
            raise HTTPException(409, "Pause automatic reviews and wait for the active review before changing connections.")
        if not settings.model:
            raise HTTPException(422, "Enter a model name.")
        if settings.provider != "paid":
            try:
                url = urlsplit(settings.base_url)
                valid = (url.scheme in {"http", "https"} and url.hostname and url.port != 0
                         and (url.scheme == "https" or url.hostname in LOCAL_HOSTS)
                         and url.username is None and url.password is None and not url.query and not url.fragment)
            except ValueError:
                valid = False
            if not valid:
                raise HTTPException(422, "Use HTTPS for remote models, or HTTP on localhost, without credentials or query parameters.")
        updates = {"LLM_PROVIDER": settings.provider, "MAX_CONCURRENT_LLM_CALLS": str(settings.max_concurrent_llm_calls)}
        if settings.provider == "brev":
            updates.update(OLLAMA_MODEL=settings.model, OLLAMA_BASE_URL=settings.base_url)
        else:
            updates["LLM_MODEL"] = settings.model
            if settings.provider == "cisco":
                updates["LLM_BASE_URL"] = settings.base_url
        for field, key in (("github_token", "GITHUB_TOKEN"), ("llm_api_key", "LLM_API_KEY")):
            secret = getattr(settings, field)
            clear = getattr(settings, f"clear_{field}")
            raw = secret.get_secret_value() if secret else ""
            if clear and raw:
                raise HTTPException(422, "Choose either a replacement key or Remove saved key.")
            if clear or raw:
                updates[key] = "" if clear else raw
        previous_provider = os.getenv("LLM_PROVIDER", "brev")
        if previous_provider in KEY_NAMES:
            updates[MODEL_NAMES[previous_provider]] = provider_config(previous_provider)["model"]
        updates[MODEL_NAMES[settings.provider]] = settings.model
        if previous_provider in KEY_NAMES and previous_provider != settings.provider:
            updates[KEY_NAMES[previous_provider]] = provider_key(previous_provider)
        api_key = updates.get("LLM_API_KEY", provider_key(settings.provider)).strip()
        previous_url = os.getenv("OLLAMA_BASE_URL" if settings.provider == "brev" else "LLM_BASE_URL", "")
        if (settings.provider != "paid" and api_key and urlsplit(public_url(previous_url)).hostname != urlsplit(settings.base_url).hostname
                and not settings.llm_api_key):
            raise HTTPException(422, "The model host changed. Enter the key for this endpoint before saving.")
        updates[KEY_NAMES[settings.provider]] = api_key
        updates["LLM_API_KEY"] = api_key
        remote_brev = settings.provider == "brev" and urlsplit(settings.base_url).hostname not in LOCAL_HOSTS
        if (settings.provider in {"cisco", "paid"} or remote_brev) and not api_key:
            raise HTTPException(422, "This provider needs an LLM API key.")
        persist_environment(env_path, updates)
        return current_settings()

    @router.post("/github")
    async def save_github(request: Request):
        try:
            settings = GitHubSettings.model_validate(await request.json())
        except (ValidationError, ValueError, UnicodeError):
            raise HTTPException(422, "Enter a single-line GitHub token.") from None
        if reviews_active():
            raise HTTPException(409, "Pause automatic reviews and wait for the active review before changing connections.")
        raw = settings.github_token.get_secret_value().strip() if settings.github_token else ""
        if any(ord(char) < 32 or ord(char) == 127 for char in raw) or (raw and settings.clear_github_token):
            raise HTTPException(422, "Choose a new token or remove the saved token.")
        if raw or settings.clear_github_token:
            persist_environment(env_path, {"GITHUB_TOKEN": "" if settings.clear_github_token else raw})
        return current_settings()

    @router.post("/test")
    async def test_connection(req: ConnectionTest):
        if req.target == "model":
            return await check_model_connection()
        from integrations.github import RepositoryInput, github_client, list_pull_requests
        if not req.repository.strip():
            raise HTTPException(422, "Enter a repository below, then check access or load its PRs.")
        async for client in github_client():
            data = await list_pull_requests(RepositoryInput(repository=req.repository), client)
            return {"ok": True, "message": f"GitHub can read {data['repository']}."}

    return router


class GitHubSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    github_token: SecretStr | None = Field(default=None, max_length=4096)
    clear_github_token: bool = False


class ConnectionTest(BaseModel):
    target: Literal["model", "github"]
    repository: str = Field(default="", max_length=2048)


async def check_model_connection():
    provider = os.getenv("LLM_PROVIDER", "brev").lower()
    if provider not in KEY_NAMES:
        raise HTTPException(422, "Choose a model connection first.")
    key = provider_key(provider)
    config = provider_config(provider)
    model, base = config["model"], config["base_url"].rstrip("/")
    if not model:
        raise HTTPException(422, "Save a model name first.")
    if provider != "paid":
        try:
            url = urlsplit(base)
            if (not url.hostname or url.scheme not in {"http", "https"} or url.username or url.password
                    or url.query or url.fragment or (url.scheme == "http" and url.hostname not in LOCAL_HOSTS)):
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "Save a secure model URL first (HTTPS or localhost HTTP).") from None
        if url.hostname not in LOCAL_HOSTS and not key:
            raise HTTPException(422, "Enter the model access key, then save the connection.")
    elif not key:
        raise HTTPException(422, "Save the provider API key first.")
    try:
        if provider == "paid":
            import litellm
            await litellm.acompletion(model=model, api_key=key, messages=[{"role": "user", "content": "Say OK"}], max_tokens=4, timeout=20)
        else:
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            path = "/api/tags" if provider == "brev" else ("/models" if base.endswith("/v1") else "/v1/models")
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.get(base + path, headers=headers)
            if response.status_code in {401, 403}:
                raise HTTPException(401, "The model rejected the access key. Check the key and save again.")
            if response.status_code != 200:
                raise HTTPException(502, "The model endpoint did not respond successfully. Check its URL and service.")
            data = response.json()
            names = {item.get("name") for item in data.get("models", [])} if provider == "brev" else {item.get("id") for item in data.get("data", [])}
            if model not in names:
                raise HTTPException(422, "The configured model is not available at this endpoint.")
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(504, "The model connection timed out. Check that the service is running, then try again.") from None
    except httpx.ConnectError:
        raise HTTPException(502, "Could not reach the model endpoint. Check its URL and that the service is running.") from None
    except Exception:
        raise HTTPException(502, "Could not verify the model. Check the connection and try again.") from None
    return {"ok": True, "message": f"Model connected: {model}."}
