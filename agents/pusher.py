"""Push agent: turn the council's PR-level TradeoffMap into a GitHub comment.

Writing to GitHub is outward-facing, so everything defaults to dry_run=True: the caller gets
the exact body that WOULD be posted and must ask again with dry_run=False to publish it.
"""
import httpx

from contracts.schema import TradeoffMap

BADGE = {
    "auto_approve": "✅ Auto-approve",
    "approve_with_changes": "🟡 Approve with changes",
    "block": "⛔ Block",
    "escalate": "🚨 Needs a human (critical risk)",
}
MARKER = "<!-- pr-council -->"  # lets a re-run find and update its own comment


def format_comment(pr_map: TradeoffMap, council_url: str | None = None) -> str:
    decision = pr_map.decision or ("escalate" if pr_map.outcome == "escalate" else "approve_with_changes")
    lines = [MARKER, f"## PR Council: {BADGE[decision]}"]
    if pr_map.confidence is not None:
        lines.append(f"_Confidence {pr_map.confidence:.0%}. Five reviewers (security, performance, compatibility, quality, product) debated each changed file; a judge made the call._")
    if pr_map.root_cause:
        lines += ["", "### Why", pr_map.root_cause]
    if pr_map.suggested_fix:
        lines += ["", "### Suggested fix", pr_map.suggested_fix]
    if pr_map.unresolved:
        lines += ["", "### Tradeoffs the reviewers disagreed on", *[f"- {u}" for u in pr_map.unresolved]]
    if pr_map.human_decisions:
        lines += ["", "### Needs a person", *[f"- {h}" for h in pr_map.human_decisions]]
    if pr_map.resolved:
        lines += ["", "<details><summary>Cleared</summary>", "", *[f"- {r}" for r in pr_map.resolved], "", "</details>"]
    if council_url:
        lines += ["", f"[Full debate]({council_url})"]
    return "\n".join(lines)


async def post_comment(
    client: httpx.AsyncClient, owner: str, repo: str, number: int, body: str, dry_run: bool = True
) -> dict:
    """Post (or, with dry_run, preview) a PR comment. `client` carries the Authorization header."""
    if dry_run:
        return {"dry_run": True, "would_post_to": f"{owner}/{repo}#{number}", "body": body}
    response = await client.post(f"/repos/{owner}/{repo}/issues/{number}/comments", json={"body": body})
    if response.status_code in (401, 403, 404):
        raise PermissionError(
            "GitHub refused the comment. The token needs Pull requests: write (or Issues: write) on this repository."
        )
    response.raise_for_status()
    return {"dry_run": False, "url": response.json()["html_url"]}
