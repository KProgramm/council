"""Deterministic, model-free risk scores for a unified diff.

These are cheap static signals, not ground truth: they let us (a) stratify the eval set by
how risky a PR looks, and (b) check that the agents' decisions track risk. Every score is 0..1.
"""
import math
import re

# (dimension, pattern) pairs matched against ADDED lines and file paths.
SECURITY = re.compile(
    r"auth|token|secret|passw|crypt|hmac|ssl|tls|cert|sanitiz|escape|inject|sql|exec\(|eval\(|"
    r"subprocess|pickle|yaml\.load|verify\s*=\s*False|chmod|permission|csrf|cookie|header|smuggl|parse", re.I)
CONCURRENCY = re.compile(r"lock|mutex|thread|async |await |race|atomic|queue|concurr|goroutine|sync\.|cache|global ", re.I)
COMPAT = re.compile(r"def \w+\(|class \w+|export |public |deprecat|remove|rename|signature|api|protocol|schema|migration", re.I)
PERF = re.compile(r"for .* in |while |\.list\(|json|copy|deepcopy|sort|query|select |regex|re\.compile|loop|O\(n", re.I)


def _files(diff: str) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current = line.rsplit(" b/", 1)[-1]
            files[current] = []
        elif current is not None:
            files[current].append(line)
    return files


def _saturate(count: float, scale: float) -> float:
    return 1 - math.exp(-count / scale)


def score(diff: str) -> dict[str, float]:
    files = _files(diff)
    added = [l[1:] for ls in files.values() for l in ls if l.startswith("+") and not l.startswith("+++")]
    removed = [l for ls in files.values() for l in ls if l.startswith("-") and not l.startswith("---")]
    paths = " ".join(files)
    tests = sum(1 for p in files if re.search(r"(^|/)(tests?|__tests__|spec)/|test_|_test\.|\.test\.|\.spec\.", p))
    code_files = max(1, len(files) - tests)
    size = len(added) + len(removed)

    def hits(rx: re.Pattern) -> int:
        return sum(1 for l in added if rx.search(l)) + len(rx.findall(paths))

    out = {
        "security": _saturate(hits(SECURITY), 12),
        "concurrency": _saturate(hits(CONCURRENCY), 12),
        "compatibility": _saturate(hits(COMPAT) + len(removed) / 4, 25),
        "performance": _saturate(hits(PERF), 15),
        # Large diffs with no accompanying tests are risky regardless of content.
        "size": _saturate(size, 400),
        "untested": 0.0 if tests else min(1.0, 0.4 + 0.1 * code_files),
        "spread": _saturate(len(files), 8),
    }
    out["overall"] = round(
        0.2 * out["security"] + 0.15 * out["concurrency"] + 0.15 * out["compatibility"]
        + 0.1 * out["performance"] + 0.2 * out["size"] + 0.1 * out["untested"] + 0.1 * out["spread"], 3)
    return {k: round(v, 3) for k, v in out.items()}
