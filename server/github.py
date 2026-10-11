"""Read-only GitHub access for PR Council (Phase B). Works on private repos via GITHUB_TOKEN.

The token is read from the environment on every call, only ever sent in the Authorization
header to api.github.com, and never logged or returned to callers.
"""
import os
import re
from dataclasses import dataclass, field

import httpx

API = "https://api.github.com"
PR_URL = re.compile(r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)")

# Files that are noise for a code review: reviewing them costs LLM calls and finds nothing.
SKIP_SUFFIXES = (".lock", ".min.js", ".min.css", ".map", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf")
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "go.sum", "Cargo.lock"}


class GitHubError(Exception):
    """A GitHub call failed; the message is safe to show to a user (never contains the token)."""


@dataclass
class PullRequest:
    owner: str
    repo: str
    number: int
    title: str
    head_sha: str
    head_ref: str
    base_ref: str
    mergeable_state: str | None
    files: list[dict] = field(default_factory=list)  # GitHub's per-file objects (filename, patch, ...)

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


def parse_pr_url(url: str) -> tuple[str, str, int]:
    match = PR_URL.match(url.strip())
    if match is None:
        raise ValueError(f"Not a GitHub pull request URL: {url!r}")
    return match["owner"], match["repo"], int(match["number"])


def is_reviewable(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name not in SKIP_NAMES and not path.lower().endswith(SKIP_SUFFIXES)


def split_diff(diff: str) -> dict[str, str]:
    """Split a unified diff into {path: that file's diff}. Order of appearance is preserved."""
    files: dict[str, list[str]] = {}
    current: str | None = None
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            # "diff --git a/old b/new": the new path is after the last " b/".
            current = line.rstrip("\n").rsplit(" b/", 1)[-1]
            files.setdefault(current, [])
        if current is not None:
            files[current].append(line)
    return {path: "".join(lines) for path, lines in files.items()}


def file_diff(file: dict) -> str | None:
    """Rebuild a reviewable diff for one entry of GET /pulls/{n}/files; None if it has no patch."""
    patch = file.get("patch")
    if not patch:  # binary, too large for the API, or a pure rename
        return None
    path = file["filename"]
    old = file.get("previous_filename", path)
    return f"diff --git a/{old} b/{path}\n--- a/{old}\n+++ b/{path}\n{patch}\n"


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_pr(url: str, client: httpx.AsyncClient | None = None) -> PullRequest:
    """Fetch PR metadata and every changed file's patch (paginated)."""
    owner, repo, number = parse_pr_url(url)
    own_client = client is None
    client = client or httpx.AsyncClient(base_url=API, headers=_headers(), timeout=30)
    try:
        meta = await _get(client, f"/repos/{owner}/{repo}/pulls/{number}")
        files: list[dict] = []
        page = 1
        while True:
            batch = await _get(client, f"/repos/{owner}/{repo}/pulls/{number}/files", per_page=100, page=page)
            files.extend(batch)
            if len(batch) < 100 or page >= 30:  # the API caps a PR at 3000 files
                break
            page += 1
    finally:
        if own_client:
            await client.aclose()
    return PullRequest(
        owner=owner,
        repo=repo,
        number=number,
        title=meta["title"],
        head_sha=meta["head"]["sha"],
        head_ref=meta["head"]["ref"],
        base_ref=meta["base"]["ref"],
        mergeable_state=meta.get("mergeable_state"),
        files=files,
    )


async def _get(client: httpx.AsyncClient, path: str, **params):
    response = await client.get(path, params=params or None)
    if response.status_code == 404:
        # GitHub answers 404 (not 403) for a private repo the token cannot see.
        raise GitHubError(
            "PR not found. For a private repository, set GITHUB_TOKEN to a token that can read it."
        )
    if response.status_code in (401, 403):
        hint = " (rate limited; set GITHUB_TOKEN)" if response.headers.get("x-ratelimit-remaining") == "0" else ""
        raise GitHubError(f"GitHub refused the request ({response.status_code}){hint}. Check GITHUB_TOKEN scopes.")
    response.raise_for_status()
    return response.json()
