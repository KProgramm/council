# PR Council React UI

React/TypeScript (Vite, Tailwind, shadcn/ui) frontend in `web-react/`. It does not replace the existing `web/` UI.

## Prerequisites

- Node.js 20+ and npm
- The Python backend set up per the root `README.md` (`.venv`, `pip install -r requirements.txt`, `.env` with your model key)
- A branch that includes the GitHub integration (`integrations/`, `server/watcher.py`, `server/settings.py`)

## Run locally

1. Install frontend dependencies once:

   ```bash
   npm --prefix web-react install
   ```

2. Start the backend from the repository root (run a single server; the review queue is locked to one process):

   ```bash
   .venv/bin/uvicorn server.app:app --reload --port 8000
   ```

3. Start the frontend in a second terminal:

   ```bash
   npm --prefix web-react run dev -- --host 127.0.0.1
   ```

4. Open <http://127.0.0.1:5173/>. Use `127.0.0.1` or `localhost`, since the API rejects other hosts.

If port 8000 is busy, start the backend on another port and point the proxy at it:

```bash
.venv/bin/uvicorn server.app:app --reload --port 8001
COUNCIL_API_TARGET=http://127.0.0.1:8001 npm --prefix web-react run dev -- --host 127.0.0.1
```

## Using it

- **Run council** reviews the pasted or uploaded diff and streams the debate.
- The **GitHub icon** in the header opens the GitHub panel: load a single PR by URL, or watch a repository (load open PRs, choose scope, start or pause the queue, view, retry or ignore reviews).
- Its **Connections** section holds the GitHub token (or public access) and the model settings. Keys are saved to your local `.env` by the backend, never by the frontend.

## How it talks to the backend

Vite proxies `/reviews`, `/ws`, `/replay`, `/fixtures`, `/github`, `/settings` and `/watch` to FastAPI (see `vite.config.ts`). The proxy also rewrites the `Origin` header, because the API only accepts same-origin requests.

## Checks

```bash
npm --prefix web-react run build   # typecheck + production build
npm --prefix web-react run lint
```

## Credits

Owl sprite by Revangale, from <https://opengameart.org/content/owl-and-raven-sprites>.
