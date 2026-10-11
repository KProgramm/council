# server/app.py — Owner: Krish (G4).
# Connects the browser to the council:
#   POST /reviews        start a review, returns {"review_id": ...}
#   WS   /ws/{review_id} stream that review's Messages as they're published
#   WS   /replay         stream fixtures/transcripts/demo.json (demo backup)
#   GET  /               the web/ UI; /fixtures/... is served too for UI step 1
import asyncio
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from contracts.schema import Message, TradeoffMap

ROOT = Path(__file__).resolve().parent.parent
DEMO_TRANSCRIPT = ROOT / "fixtures/transcripts/demo.json"
REPLAY_DELAY = 1.5  # seconds between replayed messages

app = FastAPI(title="PR Council")


class ReviewStream:
    """Everything one review has published, plus a queue per connected browser.

    The browser opens its WebSocket *after* POST /reviews returns, so some messages
    may already be out. Keeping history lets a late (or reconnecting) socket catch up.
    """

    def __init__(self):
        self.history: list[Message] = []
        self.queues: set[asyncio.Queue] = set()
        self.done = False

    async def publish(self, msg: Message) -> None:
        # Same signature as a Bus handler, so later it can be passed to bus.subscribe().
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


async def run_review(diff: str, review_id: str, stream: ReviewStream) -> None:
    """Run the real council if G1 has landed; otherwise replay the demo transcript."""
    try:
        from agents.runtime import run_council
        from transport.memory_bus import MemoryBus

        bus = MemoryBus()
        bus.subscribe(stream.publish)
        await run_council(diff, bus, review_id)
    except NotImplementedError:
        # Runtime is still a stub. Only fall back if nothing real got published,
        # so we never mix a half-run live debate with fake messages.
        if not stream.history:
            await replay_demo(stream, review_id)
    except Exception as e:
        print(f"[review {review_id}] council crashed: {e!r}")
    finally:
        stream.close()


class ReviewRequest(BaseModel):
    diff: str


# Holds references to running tasks; asyncio only keeps weak references, so an
# unreferenced background task can be garbage-collected mid-run.
_tasks: set[asyncio.Task] = set()


@app.post("/reviews")
async def create_review(req: ReviewRequest):
    review_id = uuid.uuid4().hex[:8]
    stream = streams[review_id] = ReviewStream()
    task = asyncio.create_task(run_review(req.diff, review_id, stream))
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
    await ws.accept()
    stream = streams.get(review_id)
    if stream is None:
        await ws.close(code=4404, reason="unknown review_id")
        return
    await pump(ws, stream)


@app.websocket("/replay")
async def replay_socket(ws: WebSocket):
    # Demo backup: each connection gets its own fresh playback of the fixture.
    await ws.accept()
    stream = ReviewStream()
    task = asyncio.create_task(replay_demo(stream, "demo"))
    task.add_done_callback(lambda _: stream.close())
    try:
        await pump(ws, stream)
    finally:
        task.cancel()  # stop playback if the browser disconnects early


# Static mounts go last: a mount at "/" would otherwise swallow the routes above.
app.mount("/fixtures", StaticFiles(directory=ROOT / "fixtures"), name="fixtures")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
