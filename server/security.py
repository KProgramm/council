"""Protect the local app's credentials, review controls, and private transcripts."""
from fastapi import HTTPException, Request, Response, WebSocket

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def allowed_connection(connection) -> bool:
    if (not connection.client or connection.client.host not in LOCAL_HOSTS
            or connection.url.hostname not in LOCAL_HOSTS):
        return False
    origin = connection.headers.get("origin")
    scheme = "https" if connection.url.scheme in {"https", "wss"} else "http"
    expected = f"{scheme}://{connection.url.netloc}"
    return (not origin or origin == expected) and connection.headers.get("sec-fetch-site") != "cross-site"


def local_only(request: Request, response: Response) -> None:
    if not allowed_connection(request):
        raise HTTPException(403, "Open Council on this computer using localhost or 127.0.0.1.")
    response.headers["Cache-Control"] = "no-store"


async def local_socket(ws: WebSocket) -> bool:
    if not allowed_connection(ws):
        await ws.close(code=4403)
        return False
    return True
