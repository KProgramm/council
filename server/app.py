# server/app.py — Owner: Krish (G4).
# Connects the browser to the council:
#   POST /reviews        start a review, returns {"review_id": ...}
#   WS   /ws/{review_id} stream that review's Messages as they're published
#   WS   /replay         stream fixtures/transcripts/demo.json (demo backup)
#   GET  /               the web/ UI; /fixtures/... is served too for UI step 1
import asyncio
import json
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from contracts.schema import Message, TradeoffMap
from integrations.github import router as github_router
from server.settings import create_settings_router, check_model_connection
from server.security import allowed_connection, local_socket
from server.watcher import RepositoryWatch, create_watch_router

load_dotenv()  # so BUS / SLIM_* from .env are visible to make_bus()
ROOT = Path(__file__).resolve().parent.parent
DEMO_TRANSCRIPT = ROOT / "fixtures/transcripts/demo.json"
REPLAY_DELAY = 1.5  # seconds between replayed messages

@asynccontextmanager
async def lifespan(app):
    app.state.watch = RepositoryWatch(ROOT / ".council" / "reviews.sqlite3", run_automatic_review)
    app.state.watch.start()
    try:
        yield
    finally:
        await app.state.watch.close()
        for task in list(_tasks):
            task.cancel()
        await asyncio.gather(*_tasks, return_exceptions=True)


app = FastAPI(title="PR Council", lifespan=lifespan)


@app.middleware("http")
async def protect_local_app(request, call_next):
    if not allowed_connection(request):
        return JSONResponse({"detail": "Open Council on this computer using localhost or 127.0.0.1."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response

app.include_router(github_router)


class ReviewStream:
    """Everything one review has published, plus a queue per connected browser.

    The browser opens its WebSocket *after* POST /reviews returns, so some messages
    may already be out. Keeping history lets a late (or reconnecting) socket catch up.
    """

    def __init__(self, sink=None):
        self.sink = sink
        self.error = None
        self.history: list[Message] = []
        self.queues: set[asyncio.Queue] = set()
        self.done = False

    async def publish(self, msg: Message) -> None:
        # Same signature as a Bus handler, so later it can be passed to bus.subscribe().
        if self.sink:
            await self.sink(msg)
        self.history.append(msg)
        for q in self.queues:
            q.put_nowait(msg)

    def close(self) -> None:
        self.done = True
        for q in self.queues:
            q.put_nowait(None)  # None = "no more messages"


streams: dict[str, ReviewStream] = {}


def load_demo() -> list[Message]:
    """The fixture transcript, with its tradeoff map appended as the final verdict Message.

    This is the same shape G3 will publish live: agent="system", kind="verdict",
    and the TradeoffMap JSON in `text`. So the UI handles replay and live identically.
    """
    data = json.loads(DEMO_TRANSCRIPT.read_text())
    msgs = [Message.model_validate(m) for m in data["messages"]]
    tmap = TradeoffMap.model_validate(data["tradeoff_map"])
    msgs.append(Message(
        review_id=tmap.review_id,
        round=msgs[-1].round,
        agent="system",
        kind="verdict",
        text=tmap.model_dump_json(),
    ))
    return msgs


async def replay_demo(stream: ReviewStream, review_id: str) -> None:
    for msg in load_demo():
        await stream.publish(msg.model_copy(update={"review_id": review_id}))
        await asyncio.sleep(REPLAY_DELAY)


def make_bus(review_id: str):
    """Pick the transport from .env: BUS=memory (default) or BUS=slim."""
    if os.getenv("BUS", "memory") == "slim":
        from transport.slim_bus import SlimBus

        return SlimBus(review_id)
    from transport.memory_bus import MemoryBus

    return MemoryBus()


async def run_review(diff: str, review_id: str, stream: ReviewStream, *, allow_replay=True) -> None:
    """Run the real council if G1 has landed; otherwise replay the demo transcript."""
    try:
        from agents.runtime import run_council

        bus = make_bus(review_id)
        bus.subscribe(stream.publish)
        try:
            await run_council(diff, bus, review_id)
        finally:
            if hasattr(bus, "close"):  # SlimBus holds a SLIM session open
                await bus.close()
    except NotImplementedError:
        # Runtime is still a stub. Only fall back if nothing real got published,
        # so we never mix a half-run live debate with fake messages.
        if allow_replay and not stream.history:
            await replay_demo(stream, review_id)
        else:
            stream.error = "Council runtime is unavailable."
    except Exception as e:
        stream.error = "Council could not finish this review. Check the model connection."
        print(f"[review {review_id}] council failed ({type(e).__name__})")
    finally:
        stream.close()


review_lock = asyncio.Lock()


async def run_serial_review(diff, review_id, stream, **options):
    async with review_lock:
        await run_review(diff, review_id, stream, **options)


async def run_automatic_review(diff, review_id, publish):
    stream = streams[review_id] = ReviewStream(publish)
    try:
        await run_serial_review(diff, review_id, stream, allow_replay=False)
        if stream.error:
            raise RuntimeError(stream.error)
    finally:
        # Automatic histories are durable and can be read from the queue.
        streams.pop(review_id, None)


class ReviewRequest(BaseModel):
    diff: str = Field(min_length=1, max_length=32_000)


# Holds references to running tasks; asyncio only keeps weak references, so an
# unreferenced background task can be garbage-collected mid-run.
_tasks: set[asyncio.Task] = set()


@app.post("/reviews")
async def create_review(req: ReviewRequest):
    review_id = uuid.uuid4().hex[:8]
    stream = streams[review_id] = ReviewStream()
    task = asyncio.create_task(run_serial_review(req.diff, review_id, stream))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"review_id": review_id}


async def pump(ws: WebSocket, stream: ReviewStream) -> None:
    """Send a stream's backlog, then every new message, then close the socket."""
    q: asyncio.Queue = asyncio.Queue()
    # No `await` between registering the queue and copying history: asyncio can't
    # switch tasks here, so no message can slip in between and be missed or doubled.
    stream.queues.add(q)
    backlog = list(stream.history)
    if stream.done:
        q.put_nowait(None)
    try:
        for msg in backlog:
            await ws.send_text(msg.model_dump_json())
        while (msg := await q.get()) is not None:
            await ws.send_text(msg.model_dump_json())
        await ws.close()
    except WebSocketDisconnect:
        pass  # browser went away; the review keeps running
    finally:
        stream.queues.discard(q)


@app.websocket("/ws/{review_id}")
async def review_socket(ws: WebSocket, review_id: str):
    if not await local_socket(ws):
        return
    await ws.accept()
    stream = streams.get(review_id)
    if stream is None:
        await ws.close(code=4404, reason="unknown review_id")
        return
    await pump(ws, stream)


@app.websocket("/replay")
async def replay_socket(ws: WebSocket):
    # Demo backup: each connection gets its own fresh playback of the fixture.
    if not await local_socket(ws):
        return
    await ws.accept()
    stream = ReviewStream()
    task = asyncio.create_task(replay_demo(stream, "demo"))
    task.add_done_callback(lambda _: stream.close())
    try:
        await pump(ws, stream)
    finally:
        task.cancel()  # stop playback if the browser disconnects early


# Static mounts go last: a mount at "/" would otherwise swallow the routes above.
def connections_busy():
    watch = getattr(app.state, "watch", None)
    return any(not task.done() for task in _tasks) or bool(watch and (watch.active or watch.config()["enabled"]))


app.include_router(create_settings_router(ROOT / ".env", connections_busy))
app.include_router(create_watch_router(lambda: app.state.watch, check_model_connection))
app.mount("/fixtures", StaticFiles(directory=ROOT / "fixtures"), name="fixtures")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
