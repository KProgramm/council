"""Read GitHub PR metadata and unified diffs without executing repository code."""
import os
import re
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/github", tags=["GitHub"])
MAX_DIFF_BYTES = 32_000
MAX_DIFF_LINES = 400
PR_PATH = re.compile(
    r"/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)"
    r"(?:\.diff|/(?:files|commits))?/?"
)
REPO_PATH = re.compile(r"/?([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)/?")


class PullRequestInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    known_revision: str | None = Field(default=None, max_length=130)


class RepositoryInput(BaseModel):
    repository: str = Field(min_length=1, max_length=2048)


def parse_repository(value: str) -> tuple[str, str]:
    value = value.strip()
    try:
        if "://" in value:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}
                    or parsed.port not in {None, 443} or parsed.username is not None):
                raise ValueError("Invalid host")
            value = parsed.path
        match = REPO_PATH.fullmatch(value)
        if not match or match[2] in {".", ".."}:
            raise ValueError("Invalid repository")
        return match[1], match[2]
    except ValueError:
        raise HTTPException(422, "Enter owner/repo or a GitHub repository URL.") from None


def parse_pr_url(value: str) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(value.strip())
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in {"github.com", "www.github.com"}
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
        match = PR_PATH.fullmatch(parsed.path)
    except ValueError:
        valid, match = False, None
    if not valid or not match or match[2] in {".", ".."}:
        raise HTTPException(422, "Enter a GitHub PR URL, such as https://github.com/owner/repo/pull/123.")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


async def github_client() -> AsyncIterator[httpx.AsyncClient]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "PR-Council",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=False) as client:
        yield client


def check_response(response: httpx.Response) -> None:
    status = response.status_code
    if status == 200:
        return
    if status == 404:
        raise HTTPException(404, "Repository or PR not found or inaccessible. Check the URL and your access to private repositories.")
    if status == 401:
        raise HTTPException(401, "GitHub rejected the saved token. In Connections, replace it or choose public access and save for public repositories.")
    if status == 429 or (status == 403 and response.headers.get("x-ratelimit-remaining") == "0"):
        raise HTTPException(429, "GitHub's rate limit was reached. Try later, or save a valid GitHub token in Connections for a higher limit.")
    if status == 403:
        raise HTTPException(403, "GitHub denied access. In Connections, use a token with read access to this repository's pull requests.")
    if status in {301, 302, 307, 308}:
        raise HTTPException(422, "This repository has moved. Open it on GitHub and copy its current URL.")
    raise HTTPException(502, "GitHub could not return the requested data. Try again shortly.")


async def metadata(client: httpx.AsyncClient, api_url: str, canonical_url: str, repo: str, number: int) -> dict:
    response = await client.get(api_url)
    check_response(response)
    try:
        data = response.json()
        head_sha, base_sha = data["head"]["sha"], data["base"]["sha"]
        if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40,64}", sha) for sha in (head_sha, base_sha)):
            raise ValueError("Invalid revision")
        return {
            "url": canonical_url,
            "repository": repo,
            "number": number,
            "title": str(data["title"]),
            "author": str(data["user"]["login"]),
            "state": "merged" if data.get("merged") else str(data["state"]),
            "draft": bool(data.get("draft")),
            "head_ref": str(data["head"]["ref"]),
            "base_ref": str(data["base"]["ref"]),
            "head_sha": head_sha,
            "revision": f"{base_sha}:{head_sha}",
            "changed_files": int(data["changed_files"]),
            "additions": int(data["additions"]),
            "deletions": int(data["deletions"]),
        }
    except (KeyError, TypeError, ValueError):
        raise HTTPException(502, "GitHub returned unreadable PR metadata.") from None


def oversized_diff() -> HTTPException:
    return HTTPException(413, f"This PR is too large for the local Council model (limit: {MAX_DIFF_LINES} diff lines / {MAX_DIFF_BYTES // 1000} KB). This revision will be skipped; reduce the PR size to review it.")


@router.post("/repository")
async def list_pull_requests(req: RepositoryInput, client: httpx.AsyncClient = Depends(github_client)):
    owner, repo = parse_repository(req.repository)
    try:
        data = []
        # Construct each page on the fixed API host; never follow supplied Link URLs.
        for page in range(1, 11):
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                params={"state": "open", "sort": "created", "direction": "desc", "per_page": 100, "page": page},
            )
            check_response(response)
            batch = response.json()
            if not isinstance(batch, list):
                raise ValueError("Expected a list")
            data.extend(batch)
            if len(batch) < 100:
                break
        pulls = []
        for pr in data:
            number = int(pr["number"])
            if number < 1:
                raise ValueError("Invalid PR number")
            pulls.append({
                "number": number,
                "title": str(pr["title"]),
                "url": f"https://github.com/{owner}/{repo}/pull/{number}",
                "author": str(pr["user"]["login"]),
                "draft": bool(pr.get("draft")),
                "head_sha": str(pr["head"]["sha"]),
                "revision": f"{pr['base']['sha']}:{pr['head']['sha']}",
                "created_at": str(pr["created_at"]),
            })
        return {"repository": f"{owner}/{repo}", "pull_requests": pulls, "limit": 1000, "incomplete": len(data) == 1000}
    except httpx.TimeoutException:
        raise HTTPException(504, "GitHub took too long to respond. Try again.") from None
    except httpx.HTTPError:
        raise HTTPException(502, "Could not connect to GitHub. Check your connection and try again.") from None
    except (KeyError, TypeError, ValueError):
        raise HTTPException(502, "GitHub returned an unreadable PR list.") from None


@router.post("/pull-request")
async def load_pull_request(req: PullRequestInput, client: httpx.AsyncClient = Depends(github_client)):
    owner, repo, number = parse_pr_url(req.url)
    # All outbound URLs are constructed on GitHub's API host, never fetched from user input.
    api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
    canonical_url = f"https://github.com/{owner}/{repo}/pull/{number}"
    try:
        info = await metadata(client, api_url, canonical_url, f"{owner}/{repo}", number)
        if req.known_revision == info["revision"]:
            return {"pull_request": info, "changed": False, "diff": None}
        chunks = bytearray()
        async with client.stream("GET", api_url, headers={"Accept": "application/vnd.github.diff"}) as response:
            check_response(response)
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_DIFF_BYTES:
                    raise oversized_diff()
        diff = chunks.decode("utf-8", errors="replace")
        if len(diff.splitlines()) > MAX_DIFF_LINES:
            raise oversized_diff()
        if not diff.strip():
            raise HTTPException(422, "This PR has no diff to review.")
        if not diff.startswith("diff --git "):
            raise HTTPException(502, "GitHub did not return a unified diff.")
        # A push during download must not associate an older diff with a newer commit.
        current = await metadata(client, api_url, canonical_url, f"{owner}/{repo}", number)
        if current["revision"] != info["revision"]:
            raise HTTPException(409, "The PR changed while its diff was loading. Try loading it again.")
        return {"pull_request": current, "changed": True, "diff": diff}
    except httpx.TimeoutException:
        raise HTTPException(504, "GitHub took too long to respond. Try again.") from None
    except httpx.HTTPError:
        raise HTTPException(502, "Could not connect to GitHub. Check your connection and try again.") from None
