import argparse
import asyncio
import csv
import json
import re
from pathlib import Path
from statistics import mean
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from agents.base import call_llm
from agents.runtime import run_council
from contracts.schema import Message, TradeoffMap
from transport.memory_bus import MemoryBus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "eval" / "dataset.json"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
BASELINE_PROMPT_PATH = PROJECT_ROOT / "prompts" / "baseline.md"
SUMMARY_FIELDS = (
    "file",
    "baseline_caught_root_cause",
    "council_caught_root_cause",
    "baseline_distinct_concerns",
    "council_distinct_concerns",
    "baseline_tradeoffs_surfaced",
    "council_tradeoffs_surfaced",
    "baseline_escalated",
    "council_escalated",
    "label",
    "baseline_decision",
    "council_decision",
    "baseline_correct",
    "council_correct",
)


def _load_entries(
    dataset_path: Path,
    diff_path: Path | None,
    ground_truth: str | None,
) -> list[dict[str, str]]:
    if diff_path is not None:
        if not ground_truth:
            raise ValueError("--ground-truth is required when evaluating --diff")
        try:
            relative_diff = diff_path.resolve().relative_to(PROJECT_ROOT)
            file_value = relative_diff.as_posix()
        except ValueError:
            file_value = str(diff_path.resolve())
        return [{"file": file_value, "ground_truth": ground_truth}]

    entries = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"{dataset_path} must contain a JSON list")
    if entries:
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or not entry.get("file") or not entry.get("ground_truth"):
                raise ValueError(
                    f"Dataset entry {index} must include file and ground_truth"
                )
        return entries

    if not ground_truth:
        raise ValueError(
            "The evaluation dataset is empty. Supply --ground-truth to run "
            "the demo diff, or wait for G7 to populate eval/dataset.json."
        )
    return [
        {
            "file": "fixtures/diffs/demo.diff",
            "ground_truth": ground_truth,
        }
    ]


def _resolve_diff_path(file_value: str) -> Path:
    path = Path(file_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"Evaluation diff not found: {path}")
    return path


async def _baseline_review(diff: str, review_id: str) -> Message:
    system_prompt = BASELINE_PROMPT_PATH.read_text(encoding="utf-8")
    user_content = json.dumps(
        {
            "review_id": review_id,
            "round": 1,
            "diff": diff,
            "instruction": "Review this diff and return one assessment Message.",
        },
        ensure_ascii=True,
    )
    validation_error = ""
    for attempt in range(2):
        retry_content = user_content
        if validation_error:
            retry_content += (
                "\nPrevious response failed validation: "
                f"{validation_error[:1000]}\nReturn a corrected Message JSON object."
            )
        raw_response = await call_llm(
            system_prompt,
            retry_content,
            use_cache=(attempt == 0),
        )
        try:
            payload = json.loads(raw_response)
            if not isinstance(payload, dict) or payload.get("pass") is True:
                raise ValueError("The baseline must return a Message object")
            payload.pop("id", None)
            payload.pop("ts", None)
            payload.update(review_id=review_id, agent="quality", round=1)
            message = Message.model_validate(payload)
            if message.kind != "assessment" or message.references:
                raise ValueError("The baseline must be an independent assessment")
            return message
        except (TypeError, ValueError) as error:
            validation_error = str(error)

    raise ValueError(f"Baseline response remained invalid after one retry: {validation_error}")


def _distinct_concerns(messages: list[Message]) -> int:
    unique_claims = {
        (
            claim.file,
            claim.line,
            " ".join(claim.concern.casefold().split()),
        )
        for message in messages
        for claim in message.claims
    }
    return len(unique_claims)


def _review_for_scoring(messages: list[Message]) -> dict[str, Any]:
    return {
        "messages": [
            message.model_dump(mode="json")
            for message in messages
            if message.agent != "system" and message.kind != "verdict"
        ]
    }


def _parse_yes_no(response: str) -> bool:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        answer = payload.get("answer")
        if isinstance(answer, str) and answer.strip().casefold() in {"yes", "no"}:
            return answer.strip().casefold() == "yes"

    match = re.match(r"\s*(yes|no)\b", response, re.IGNORECASE)
    if match is None:
        raise ValueError(f"Root-cause evaluator must answer yes or no, got: {response!r}")
    return match.group(1).casefold() == "yes"


async def _score_root_cause(ground_truth: str, review: dict[str, Any]) -> bool:
    evaluator_prompt = (
        "You are a strict evaluation judge. Decide whether the review explicitly "
        "identifies the stated root cause. Respond with only a JSON object of the form "
        '{"answer": "yes"} or {"answer": "no"}. Do not give credit '
        "for a related concern that does not identify the root cause."
    )
    response = await call_llm(
        evaluator_prompt,
        json.dumps(
            {"ground_truth": ground_truth, "review": review},
            ensure_ascii=True,
        ),
    )
    return _parse_yes_no(response)


def _extract_tradeoff_map(transcript: list[Message]) -> TradeoffMap:
    verdicts = [
        message
        for message in transcript
        if message.agent == "system" and message.kind == "verdict"
    ]
    if not verdicts:
        raise ValueError("Council transcript did not contain a verdict message")
    return TradeoffMap.model_validate_json(verdicts[-1].text)


_POSITION_TO_DECISION = {
    "approve": "auto_approve",
    "approve_with_changes": "approve_with_changes",
    "block": "block",
    "escalate": "escalate",
}


def _decision_for(messages: list[Message], tradeoff_map: TradeoffMap | None) -> str:
    """The run's decision: the council's map, or the single baseline reviewer's position."""
    if tradeoff_map is not None:
        if tradeoff_map.decision is not None:
            return tradeoff_map.decision
        return "escalate" if tradeoff_map.outcome == "escalate" else "approve_with_changes"
    positions = [m.position for m in messages if m.agent != "system" and m.position]
    return _POSITION_TO_DECISION.get(positions[-1], "escalate") if positions else "escalate"


def _is_correct(label: str, decision: str, caught_root_cause: bool) -> bool:
    """Was the decision right for this PR?

    A bad (reverted) PR is handled correctly by blocking it, or by letting it through with
    changes only if the review actually named the root cause. A good PR is handled correctly
    when it may merge. Escalating is never correct: it hands the decision back to a human.
    This keeps "always block" and "always approve_with_changes" from scoring well.
    """
    if label == "bad":
        return decision == "block" or (decision == "approve_with_changes" and caught_root_cause)
    return decision in {"auto_approve", "approve_with_changes"}


def _score_run(
    messages: list[Message],
    caught_root_cause: bool,
    tradeoff_map: TradeoffMap | None,
    label: str = "bad",
) -> dict[str, Any]:
    reviewer_messages = [message for message in messages if message.agent != "system"]
    decision = _decision_for(messages, tradeoff_map)
    return {
        "decision": decision,
        "correct": _is_correct(label, decision, caught_root_cause),
        "caught_root_cause": caught_root_cause,
        "distinct_concerns": _distinct_concerns(reviewer_messages),
        "tradeoffs_surfaced": len(tradeoff_map.unresolved) if tradeoff_map else 0,
        "escalated": tradeoff_map.outcome == "escalate"
        if tradeoff_map
        else any(message.position == "escalate" for message in reviewer_messages),
    }


async def _evaluate_entry(entry: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    diff_path = _resolve_diff_path(entry["file"])
    diff = diff_path.read_text(encoding="utf-8")
    review_id = diff_path.stem
    ground_truth = entry["ground_truth"]
    label = entry.get("label", "bad")

    baseline_message = await _baseline_review(diff, review_id)
    baseline_review = _review_for_scoring([baseline_message])
    # A good PR has no root cause to find, so skip the judge call (and its cost) for those.
    baseline_caught = label == "bad" and await _score_root_cause(ground_truth, baseline_review)
    baseline_scores = _score_run([baseline_message], baseline_caught, None, label)

    council_transcript = await run_council(diff, MemoryBus(), review_id)
    tradeoff_map = _extract_tradeoff_map(council_transcript)
    council_review = _review_for_scoring(council_transcript)
    council_caught = label == "bad" and await _score_root_cause(ground_truth, council_review)
    council_scores = _score_run(council_transcript, council_caught, tradeoff_map, label)

    name = entry.get("name") or diff_path.stem
    result = {
        "file": entry["file"],
        "label": label,
        "pr_url": entry.get("pr_url"),
        "ground_truth": ground_truth,
        "baseline": {
            "message": baseline_message.model_dump(mode="json"),
            "scores": baseline_scores,
        },
        "council": {
            "messages": [message.model_dump(mode="json") for message in council_transcript],
            "tradeoff_map": tradeoff_map.model_dump(mode="json"),
            "scores": council_scores,
        },
    }
    summary_row = {
        "file": entry["file"],
        "baseline_caught_root_cause": baseline_scores["caught_root_cause"],
        "council_caught_root_cause": council_scores["caught_root_cause"],
        "baseline_distinct_concerns": baseline_scores["distinct_concerns"],
        "council_distinct_concerns": council_scores["distinct_concerns"],
        "baseline_tradeoffs_surfaced": baseline_scores["tradeoffs_surfaced"],
        "council_tradeoffs_surfaced": council_scores["tradeoffs_surfaced"],
        "baseline_escalated": baseline_scores["escalated"],
        "council_escalated": council_scores["escalated"],
        "label": label,
        "baseline_decision": baseline_scores["decision"],
        "council_decision": council_scores["decision"],
        "baseline_correct": baseline_scores["correct"],
        "council_correct": council_scores["correct"],
    }
    return {"name": name, "result": result}, summary_row


def _write_summary(rows: list[dict[str, Any]], results_dir: Path) -> None:
    summary_path = results_dir / "summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=SUMMARY_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _rate(rows: list[dict[str, Any]], column: str, where=lambda row: True) -> float:
    selected = [row for row in rows if where(row)]
    return mean(_truthy(row[column]) for row in selected) if selected else 0.0


def _truthy(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).lower() == "true"


def metrics(rows: list[dict[str, Any]], side: str) -> dict[str, float]:
    """The headline numbers for "baseline" or "council"."""
    bad = lambda row: row["label"] == "bad"
    good = lambda row: row["label"] == "good"
    return {
        "Decision accuracy": _rate(rows, f"{side}_correct"),
        "Root cause found (bad PRs)": _rate(rows, f"{side}_caught_root_cause", bad),
        "Escalation rate": _rate(rows, f"{side}_escalated"),
        "False-block rate (good PRs)": mean(
            row[f"{side}_decision"] == "block" for row in rows if good(row)
        )
        if any(good(row) for row in rows)
        else 0.0,
    }


def _write_chart(rows: list[dict[str, Any]], results_dir: Path) -> None:
    baseline = metrics(rows, "baseline")
    council = metrics(rows, "council")
    labels = list(baseline)
    positions = range(len(labels))
    width = 0.36

    figure, axis = plt.subplots(figsize=(10, 5))
    baseline_bars = axis.bar(
        [position - width / 2 for position in positions],
        list(baseline.values()),
        width,
        label="Single reviewer",
        color="#d87942",
    )
    council_bars = axis.bar(
        [position + width / 2 for position in positions],
        list(council.values()),
        width,
        label="Council",
        color="#327d73",
    )
    axis.set_xticks(list(positions), labels)
    axis.set_ylim(0, 1.1)
    axis.set_ylabel("Rate (accuracy / root cause: higher is better; escalation / false-block: lower)")
    axis.set_title(f"Single reviewer vs. council ({len(rows)} PRs)")
    axis.legend()
    axis.bar_label(baseline_bars, fmt="%.2f", padding=3)
    axis.bar_label(council_bars, fmt="%.2f", padding=3)
    figure.tight_layout()
    figure.savefig(results_dir / "chart.png", dpi=160)
    plt.close(figure)


async def run_evaluation(
    entries: list[dict[str, str]],
    results_dir: Path = RESULTS_DIR,
) -> list[dict[str, Any]]:
    if not entries:
        raise ValueError("At least one evaluation entry is required")
    results_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    used_names: set[str] = set()

    for entry in entries:
        evaluated, summary_row = await _evaluate_entry(entry)
        name = evaluated["name"]
        if name in used_names:
            raise ValueError(f"Duplicate evaluation result name: {name}")
        used_names.add(name)
        result_path = results_dir / f"{name}.json"
        result_path.write_text(
            json.dumps(evaluated["result"], indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        summary_rows.append(summary_row)

    _write_summary(summary_rows, results_dir)
    _write_chart(summary_rows, results_dir)
    return summary_rows


async def _async_main(arguments: argparse.Namespace) -> None:
    entries = _load_entries(
        dataset_path=arguments.dataset,
        diff_path=arguments.diff,
        ground_truth=arguments.ground_truth,
    )
    if arguments.split:
        entries = [e for e in entries if e.get("split") == arguments.split]
        if not entries:
            raise ValueError(f"No dataset entries have split={arguments.split!r}")
    rows = await run_evaluation(entries, arguments.results_dir)
    for side in ("baseline", "council"):
        print(side, {k: round(v, 2) for k, v in metrics(rows, side).items()})
    print(f"Evaluated {len(rows)} diff(s); wrote results to {arguments.results_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare a single review with PR Council")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--diff", type=Path, help="Evaluate one diff instead of the dataset")
    parser.add_argument(
        "--ground-truth",
        help="One-sentence root cause, required for --diff or an empty dataset",
    )
    parser.add_argument(
        "--split",
        choices=["dev", "test"],
        help="Only entries with this split (tune prompts on dev, report on test)",
    )
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    asyncio.run(_async_main(parser.parse_args()))


if __name__ == "__main__":
    main()