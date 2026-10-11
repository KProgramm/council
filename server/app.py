# server/app.py — Owner: Krish (G4).
# Connects the browser to the council:
#   POST /reviews        start a review, returns {"review_id": ...}
#   WS   /ws/{review_id} stream that review's Messages as they're published
#   GET  /               the web/ UI
import asyncio
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from contracts.schema import Message
from agents.validation import InvalidReviewResponse
from integrations.github import router as github_router
from server.settings import create_settings_router, check_model_connection
from server.security import allowed_connection, local_socket
from server.watcher import RepositoryWatch, create_watch_router

load_dotenv()  # so BUS / SLIM_* from .env are visible to make_bus()
ROOT = Path(__file__).resolve().parent.parent

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
        self.error_type = RuntimeError
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


def make_bus(review_id: str):
    """Pick the transport from .env: BUS=memory (default) or BUS=slim."""
    if os.getenv("BUS", "memory") == "slim":
        from transport.slim_bus import SlimBus

        return SlimBus(review_id)
    from transport.memory_bus import MemoryBus

    return MemoryBus()


async def run_review(diff: str, review_id: str, stream: ReviewStream) -> None:
    """Run the configured AI council; failures are never replaced with fixture data."""
    try:
        from agents.runtime import run_council

        bus = make_bus(review_id)
        bus.subscribe(stream.publish)
        try:
            await run_council(diff, bus, review_id)
        finally:
            if hasattr(bus, "close"):  # SlimBus holds a SLIM session open
                await bus.close()
    except InvalidReviewResponse as error:
        stream.error = str(error)
        stream.error_type = InvalidReviewResponse
    except NotImplementedError:
        stream.error = "Council runtime is unavailable."
    except Exception as e:
        stream.error = "Council could not finish this review. Check the model connection."
        print(f"[review {review_id}] council failed ({type(e).__name__})")
    finally:
        stream.close()


review_lock = asyncio.Lock()


async def run_serial_review(diff, review_id, stream):
    async with review_lock:
        await run_review(diff, review_id, stream)


async def run_automatic_review(diff, review_id, publish):
    stream = streams[review_id] = ReviewStream(publish)
    try:
        await run_serial_review(diff, review_id, stream)
        if stream.error:
            raise stream.error_type(stream.error)
    finally:
        # Automatic histories are durable and can be read from the queue.
        streams.pop(review_id, None)


class ReviewRequest(BaseModel):
    diff: str = Field(min_length=1, max_length=32_000)


# Holds references to running tasks; asyncio only keeps weak references, so an
# unreferenced background task can be garbage-collected mid-run.
_tasks: set[asyncio.Task] = set()
_review_tasks: dict[str, asyncio.Task] = {}  # review_id -> its task, so the Stop button can cancel it


@app.post("/reviews")
async def create_review(req: ReviewRequest):
    await check_model_connection()
    review_id = uuid.uuid4().hex[:8]
    stream = streams[review_id] = ReviewStream()
    task = asyncio.create_task(run_serial_review(req.diff, review_id, stream))
    _tasks.add(task)
    _review_tasks[review_id] = task
    task.add_done_callback(_tasks.discard)
    task.add_done_callback(lambda _t, rid=review_id: _review_tasks.pop(rid, None))
    return {"review_id": review_id}


@app.post("/reviews/{review_id}/stop")
async def stop_review(review_id: str):
    """Cancel a running review mid-debate. Cancelling the task also cancels in-flight reviewer calls
    (agents/runtime.py cancels its per-round tasks) and closes the stream so the browser's socket ends."""
    task = _review_tasks.get(review_id)
    if task is None or task.done():
        return {"stopped": False}
    task.cancel()
    stream = streams.get(review_id)
    if stream is not None:
        stream.error = "Stopped."
        stream.close()
    return {"stopped": True}


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
        await ws.close(code=1011 if stream.error else 1000, reason=stream.error or "")
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


# Static mounts go last: a mount at "/" would otherwise swallow the routes above.
def connections_busy():
    watch = getattr(app.state, "watch", None)
    return any(not task.done() for task in _tasks) or bool(watch and (watch.active or watch.config()["enabled"]))


app.include_router(create_settings_router(ROOT / ".env", connections_busy))
app.include_router(create_watch_router(lambda: app.state.watch, check_model_connection))
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
