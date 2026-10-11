import asyncio
import hashlib
import json
import os
from pathlib import Path

from contracts.bus import Bus
from contracts.schema import AgentName, Message


_semaphore: asyncio.Semaphore | None = None
_semaphore_limit: int | None = None


def _load_environment() -> None:
    from dotenv import load_dotenv

    load_dotenv()


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_limit
    _load_environment()
    limit = max(1, int(os.getenv("MAX_CONCURRENT_LLM_CALLS", "3")))
    if _semaphore is None or _semaphore_limit != limit:
        _semaphore = asyncio.Semaphore(limit)
        _semaphore_limit = limit
    return _semaphore


def _provider_settings() -> tuple[str, dict[str, str]]:
    _load_environment()
    provider = os.getenv("LLM_PROVIDER", "brev").lower()
    if provider == "brev":
        model = f"ollama/{os.environ['OLLAMA_MODEL']}"
        options = {"api_base": os.environ["OLLAMA_BASE_URL"]}
    elif provider == "cisco":
        model = f"litellm_proxy/{os.environ['LLM_MODEL']}"
        options = {
            "api_base": os.environ["LLM_BASE_URL"],
            "api_key": os.environ["LLM_API_KEY"],
        }
    elif provider == "paid":
        model = os.environ["LLM_MODEL"]
        options = {"api_key": os.environ["LLM_API_KEY"]}
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")
    return model, options


async def call_llm(system_prompt: str, user_content: str, use_cache: bool = True) -> str:
    model, options = _provider_settings()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    cache_payload = json.dumps(
        {"model": model, "options": options, "messages": messages},
        sort_keys=True,
    )
    cache_key = hashlib.sha256(cache_payload.encode("utf-8")).hexdigest()
    cache_path = Path(__file__).resolve().parents[1] / ".cache" / f"{cache_key}.json"

    if use_cache:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached.get("response"), str):
                return cached["response"]
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    import litellm

    async with _get_semaphore():
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            **options,
        )
    content = response.choices[0].message.content
    if not isinstance(content, str):
        return ""

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"response": content}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return content


class ReviewAgent:
    """One reviewer. Reads prompts/{name}.md every round, hears every bus message, decides whether to speak."""

    def __init__(self, name: AgentName, bus: Bus):
        self.name = name
        self.bus = bus
        self.transcript: list[Message] = []
        self.bus.subscribe(self._receive)

    async def _receive(self, msg: Message) -> None:
        self.transcript.append(msg)

    async def _complete(self, system_prompt: str, user_content: str, use_cache: bool = True) -> str:
        return await call_llm(system_prompt, user_content, use_cache=use_cache)

    async def speak(self, round_num: int) -> Message | None:
        diff_message = next(
            (
                msg
                for msg in self.transcript
                if msg.agent == "system" and msg.kind == "diff"
            ),
            None,
        )
        if diff_message is None:
            raise RuntimeError("The diff must be published before an agent can speak")

        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / f"{self.name}.md"
        system_prompt = prompt_path.read_text(encoding="utf-8")
        transcript_json = json.dumps(
            [msg.model_dump(mode="json") for msg in self.transcript],
            ensure_ascii=True,
        )
        user_content = (
            f"Diff:\n{diff_message.text}\n\n"
            f"Transcript so far (JSON):\n{transcript_json}\n\n"
            f'This is round {round_num}. Respond with a Message JSON, or '
            '{"pass": true} if you have nothing new.'
        )

        for attempt in range(2):
            try:
                raw_response = await self._complete(
                    system_prompt,
                    user_content,
                    use_cache=(attempt == 0),
                )
                payload = json.loads(raw_response)
                if isinstance(payload, dict) and payload.get("pass") is True:
                    return None
                if not isinstance(payload, dict):
                    continue
                payload.pop("id", None)
                payload.pop("ts", None)
                payload.update(
                    review_id=diff_message.review_id,
                    agent=self.name,
                    round=round_num,
                )
                return Message.model_validate(payload)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return None
