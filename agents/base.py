import asyncio
import hashlib
import json
import os
from pathlib import Path

from contracts.bus import Bus
from contracts.schema import AgentName, Message
from agents.validation import InvalidReviewResponse, validate_assessment


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


def _provider_settings() -> tuple[str, dict]:
    _load_environment()
    from integrations.model_config import runtime_settings
    return runtime_settings()


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

    if cache_path.parent.exists():
        cache_path.parent.chmod(0o700)
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
        cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache_path.parent.chmod(0o700)
        cache_path.write_text(
            json.dumps({"response": content}),
            encoding="utf-8",
        )
        cache_path.chmod(0o600)
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
        # Keep the round's input fixed while other reviewers finish and stream their output.
        transcript = list(self.transcript)
        diff_message = next(
            (
                msg
                for msg in transcript
                if msg.agent == "system" and msg.kind == "diff"
            ),
            None,
        )
        if diff_message is None:
            raise RuntimeError("The diff must be published before an agent can speak")

        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / f"{self.name}.md"
        system_prompt = prompt_path.read_text(encoding="utf-8")
        system_prompt += '\n\n' + (prompt_path.parent / 'review_protocol.md').read_text(encoding='utf-8')
        transcript_json = json.dumps(
            [msg.model_dump(mode="json") for msg in transcript],
            ensure_ascii=True,
        )
        user_content = (
            f"Diff:\n{diff_message.text}\n\n"
            f"Transcript so far (JSON):\n{transcript_json}\n\n"
            f'This is round {round_num}. Respond with a Message JSON, or '
            '{"pass": true} if you have nothing new.'
        )

        validation_error = ''
        for attempt in range(2):
            try:
                retry_content = user_content
                if validation_error:
                    retry_content += '\n\nCorrect your previous response: ' + validation_error
                raw_response = await self._complete(
                    system_prompt,
                    retry_content,
                    use_cache=(attempt == 0),
                )
                payload = json.loads(raw_response)
                if isinstance(payload, dict) and payload.get("pass") is True:
                    if round_num == 1:
                        raise ValueError('Round 1 requires an independent assessment, including position and confidence.')
                    return None
                if not isinstance(payload, dict):
                    raise ValueError('Return a JSON object with all required reviewer fields.')
                payload.pop("id", None)
                payload.pop("ts", None)
                payload.update(
                    review_id=diff_message.review_id,
                    agent=self.name,
                    round=round_num,
                )
                message = Message.model_validate(payload)
                validate_assessment(message, transcript)
                return message
            except (json.JSONDecodeError, TypeError):
                validation_error = 'Return a single valid JSON object with the required fields.'
            except ValueError as error:
                from pydantic import ValidationError
                validation_error = 'Use the required Message fields and allowed values.' if isinstance(error, ValidationError) else str(error)
        raise InvalidReviewResponse(f'The {self.name} reviewer returned invalid output twice. Try a different model or retry.')
