#!/usr/bin/env python3
"""Ablate eager Markdown enumeration without touching user data or running AI."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sjtu_learning_assistant.knowledge_compiler import KnowledgeCompilerService

BASELINE = "3aa54d1a76fd8603dcc43eef5629eb5ae3cc37dd"
REPEATS = 7
LIMIT = 1000


def no_ai(*_args):
    raise AssertionError("This experiment must never invoke AI.")


class SyntheticTree:
    """Count Path objects yielded; no actual directory or filesystem reads."""

    def __init__(self, count: int):
        self.count = count
        self.yielded = 0

    def rglob(self, pattern: str):
        assert pattern == "*.md"
        for index in range(self.count):
            self.yielded += 1
            yield Path("synthetic") / f"note-{self.count - index:07d}.md"


def original_service():
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE}:sjtu_learning_assistant/knowledge_compiler.py"],
        cwd=ROOT,
    )
    module = types.ModuleType("_source_scan_baseline")
    exec(compile(source, "<pinned-main:knowledge_compiler.py>", "exec"), module.__dict__)
    return module.KnowledgeCompilerService(ai_runner=no_ai)


def outcome(service, root):
    try:
        result = service._source_files(root)
        return {"status": "accepted", "paths": [str(path.relative_to(root)) for path in result]}
    except RuntimeError as exc:
        return {"status": "rejected", "message": str(exc)}


def compare_case(label, factories):
    samples = {name: [] for name in factories}
    outputs = {}
    counts = {}
    peaks = {}
    rng = random.Random(20261024)
    for name, factory in factories.items():
        service, tree = factory()
        outputs[name] = outcome(service, tree)  # warm-up, valid files in OS cache
    for _ in range(REPEATS):
        order = list(factories)
        rng.shuffle(order)
        for name in order:
            service, tree = factories[name]()
            gc.collect()
            start = time.perf_counter()
            observed = outcome(service, tree)
            samples[name].append((time.perf_counter() - start) * 1000)
            assert observed == outputs[name]
            counts[name] = getattr(tree, "yielded", None)
    for name, factory in factories.items():
        service, tree = factory()
        gc.collect()
        tracemalloc.start()
        outcome(service, tree)
        _, peaks[name] = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert outputs["baseline"] == outputs["bounded"]
    return {
        "case": label,
        "outputs_identical": True,
        "output_sha256": hashlib.sha256(json.dumps(outputs["baseline"], sort_keys=True).encode()).hexdigest(),
        "variants": {
            name: {
                "yielded_candidates": counts[name],
                "median_ms": round(statistics.median(samples[name]), 4),
                "samples_ms": [round(value, 4) for value in samples[name]],
                "peak_tracemalloc_bytes": peaks[name],
            }
            for name in factories
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    baseline = original_service()
    bounded = KnowledgeCompilerService(ai_runner=no_ai)
    rows = []
    with tempfile.TemporaryDirectory(prefix="sjtu-source-scan-") as directory:
        root = Path(directory).resolve()
        for count in (10, 100, 1000):
            for index in range(len(list(root.iterdir())), count):
                (root / f"note-{index:04d}.md").write_text("# Synthetic note\n", encoding="utf-8")
            rows.append(compare_case(f"valid_warm_filesystem_{count}", {
                "baseline": lambda: (baseline, root),
                "bounded": lambda: (bounded, root),
            }))
    for count in (1001, 10_000, 100_000):
        rows.append(compare_case(f"oversize_synthetic_iterator_{count}", {
            "baseline": lambda: (baseline, SyntheticTree(count)),
            "bounded": lambda: (bounded, SyntheticTree(count)),
        }))
    report = {
        "baseline_revision": BASELINE,
        "python": platform.python_version(),
        "method": {
            "limit": LIMIT,
            "repeats": REPEATS,
            "variant_order": "Paired, deterministically shuffled per repeat; one unmeasured warm-up.",
            "timing": "Wall time without tracemalloc; Python allocation peak in a separate invocation.",
            "valid": "Actual synthetic Markdown files in a temporary directory, warm OS filesystem cache.",
            "oversize": "Synthetic lazy Path iterator; yielded counts, CPU and allocations only, not actual filesystem traversal latency.",
            "gate": "Identical accepted sorted paths or identical rejection error; no security or file-size checks removed.",
            "limits": "An OS rglob implementation may buffer directory entries internally; the cap bounds consumed candidates, not all underlying scandir buffers. No App-wide latency claim.",
        },
        "results": rows,
    }
    data = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding="utf-8")
    else:
        print(data, end="")


if __name__ == "__main__":
    main()
