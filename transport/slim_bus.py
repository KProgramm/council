# transport/slim_bus.py — Owner: Krish (G6).
#
# Bus over a Cisco AGNTCY SLIM *group session*. Built directly on `slim_bindings` (the same
# library Lungo's agntcy_app_sdk wraps) because the SDK's group-chat helper is a one-shot
# "supervisor asks, participants reply until end_message" flow, not a many-to-many channel.
#
# What we verified against a real SLIM node (see PROGRESS.md):
#   - in a group session every member hears every OTHER member's message, never its own
#   - an app has to be invited by the session creator (the "moderator") to join
#   - the global SLIM service accepts only ONE connect_async per endpoint, so apps share it
#
# So one SlimBus = two SLIM apps in this process:
#   hub  creates the group session for `review_id` and publishes into it
#   tap  is invited into the group; whatever it receives is decoded and fanned out to handlers
# Every Message therefore really travels hub -> SLIM server -> tap before any handler sees it.
import asyncio
import datetime
import os
from typing import Awaitable, Callable

from contracts.schema import Message

Handler = Callable[[Message], Awaitable[None]]

DEFAULT_ENDPOINT = "http://localhost:46357"
ORG, NAMESPACE = "council", "reviews"

# connect_async can only be called once per endpoint on the global service, so every
# SlimBus in the process shares this connection.
_conn_lock = asyncio.Lock()
_conn_id: int | None = None


def encode(msg: Message) -> bytes:
    """Message -> bytes for the wire."""
    return msg.model_dump_json().encode("utf-8")


def decode(raw: bytes | str) -> Message:
    """Wire payload -> Message. Raises pydantic.ValidationError on non-Message payloads."""
    return Message.model_validate_json(raw)


async def _shared_connection(sb, endpoint: str) -> int:
    global _conn_id
    async with _conn_lock:
        if _conn_id is None:
            if not sb.is_initialized():
                sb.initialize_with_defaults()
            svc = sb.get_global_service()
            _conn_id = await svc.connect_async(sb.new_insecure_client_config(endpoint))
        return _conn_id


class SlimBus:
    """Bus over a SLIM group channel named after the review_id."""

    def __init__(self, review_id: str, endpoint: str | None = None, secret: str | None = None):
        self.review_id = review_id
        self.endpoint = endpoint or os.getenv("SLIM_ENDPOINT", DEFAULT_ENDPOINT)
        # Must equal the PASSWORD the SLIM server container was started with.
        self.secret = secret or os.getenv("SLIM_SHARED_SECRET")
        self.handlers: list[Handler] = []
        self._start_lock = asyncio.Lock()
        self._started = False
        self._sb = None  # slim_bindings module, imported lazily so BUS=memory doesn't need it
        self._hub = None
        self._hub_session = None
        self._tap_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Event] = {}  # msg.id -> set once handlers have run

    def subscribe(self, handler: Handler) -> None:
        self.handlers.append(handler)

    async def publish(self, msg: Message) -> None:
        await self._ensure_started()
        done = self._pending.setdefault(msg.id, asyncio.Event())
        await self._hub_session.publish_async(encode(msg), None, None)
        # Don't return until the tap has delivered it to every handler. MemoryBus.publish
        # also finishes only after handlers run, and the round logic relies on that ordering.
        try:
            await asyncio.wait_for(done.wait(), timeout=30)
        finally:
            self._pending.pop(msg.id, None)

    async def close(self) -> None:
        """Stop the tap and delete the group session."""
        if self._tap_task:
            self._tap_task.cancel()
        if self._hub and self._hub_session:
            try:
                handle = await self._hub.delete_session_async(self._hub_session)
                await handle.wait_async()
            except Exception:
                pass  # session may already be gone
        self._started = False

    # ---- internals -------------------------------------------------------------------

    def _identity(self, sb, name: str):
        """(Name, provider, verifier) for a shared-secret SLIM identity."""
        if not self.secret:
            raise RuntimeError("SLIM_SHARED_SECRET must be set (same value as the SLIM server's PASSWORD)")
        n = sb.Name(ORG, NAMESPACE, name)
        provider = sb.IdentityProviderConfig.SHARED_SECRET(id=str(n), data=self.secret)
        verifier = sb.IdentityVerifierConfig.SHARED_SECRET(id=str(n), data=self.secret)
        return n, provider, verifier

    async def _ensure_started(self) -> None:
        async with self._start_lock:  # two concurrent first-publishes must not both set up
            if self._started:
                return
            import slim_bindings as sb

            self._sb = sb
            conn = await _shared_connection(sb, self.endpoint)
            svc = sb.get_global_service()

            async def make_app(suffix: str):
                name, prov, ver = self._identity(sb, f"{self.review_id}-{suffix}")
                app = svc.create_app(name, prov, ver)
                await app.subscribe_async(name, conn)
                return name, app

            hub_name, self._hub = await make_app("hub")
            tap_name, tap = await make_app("tap")

            # The tap must be listening BEFORE it is invited, or the invite has no taker.
            joined: asyncio.Future = asyncio.get_running_loop().create_future()
            self._tap_task = asyncio.create_task(self._tap_loop(tap, joined))

            ctx = await self._hub.create_session_async(
                sb.SessionConfig(
                    session_type=sb.SessionType.GROUP,
                    max_retries=20,
                    interval=datetime.timedelta(seconds=10),
                    enable_mls=True,
                    metadata={},
                ),
                sb.Name(ORG, NAMESPACE, self.review_id),  # the channel
            )
            await ctx.completion.wait_async()
            self._hub_session = ctx.session

            await self._hub.set_route_async(tap_name, conn)
            invite = await self._hub_session.invite_async(tap_name)
            await invite.wait_async()
            await asyncio.wait_for(joined, timeout=15)
            self._started = True

    async def _tap_loop(self, tap, joined: asyncio.Future) -> None:
        """Wait to be invited, then decode everything that arrives and fan out to handlers."""
        sb = self._sb
        session = None
        while session is None:
            try:
                session = await tap.listen_for_session_async(timeout=datetime.timedelta(seconds=5))
            except asyncio.CancelledError:
                raise
            except Exception:
                continue  # listen timeout, keep waiting
        if not joined.done():
            joined.set_result(None)
        while True:
            try:
                raw = await session.get_message_async(datetime.timedelta(seconds=5))
            except asyncio.CancelledError:
                raise
            except sb.SlimError.SessionError as e:
                # A receive TIMEOUT is also a SessionError ("receive timeout waiting for
                # message"), and it happens whenever the LLMs are thinking. Only a real
                # close should end the loop, or the tap quits and the group session drops.
                text = str(e).lower()
                if "closed" in text or "dropped" in text:
                    return
                continue
            except Exception:
                continue
            await self._on_receive(raw.payload)

    async def _on_receive(self, raw: bytes | str) -> None:
        """Decode one payload and run every handler. Ignores non-Message traffic."""
        try:
            msg = decode(raw)
        except Exception:
            return
        try:
            await asyncio.gather(*(h(msg) for h in self.handlers))
        finally:
            event = self._pending.get(msg.id)
            if event:
                event.set()
