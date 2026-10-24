#!/usr/bin/env python3
"""Reproducible baseline comparison for subtitle long-cue splitting."""
from __future__ import annotations

import importlib.util
import json
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
BASELINE_COMMIT = "3aa54d1a76fd8603dcc43eef5629eb5ae3cc37dd"
SOURCE = "sjtu_learning_assistant/transcript_pipeline.py"
SEED = 20261024
LIMIT = 5880
REPEATS = 3


def canonical(cues: Any) -> list[dict[str, Any]]:
    return [cue.as_dict() for cue in cues]


def measure(splitter: Callable[..., Any], cue: Any) -> dict[str, Any]:
    durations: list[float] = []
    peaks: list[int] = []
    output: list[dict[str, Any]] | None = None
    for _ in range(REPEATS):
        tracemalloc.start()
        started = time.perf_counter()
        parts = splitter(cue, LIMIT)
        durations.append(time.perf_counter() - started)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
        output = canonical(parts)
    return {
        "median_ms": round(statistics.median(durations) * 1000, 3),
        "peak_memory_bytes_median": int(statistics.median(peaks)),
        "parts": output,
    }


def baseline_module(temp_root: Path):
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE_COMMIT}:{SOURCE}"], cwd=ROOT
    )
    module_path = temp_root / "baseline_transcript_pipeline.py"
    module_path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("baseline_transcript_pipeline", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load baseline module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, module_path


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from sjtu_learning_assistant import transcript_pipeline as current

    results: dict[str, Any] = {
        "baseline_commit": BASELINE_COMMIT,
        "seed": SEED,
        "limit_chars": LIMIT,
        "repeats": REPEATS,
        "method": "Baseline loaded from git show; exact cue dictionaries compared; median time and tracemalloc peak; copy volume is a deterministic slice-length calculation from each implementation.",
        "long_cue_scenarios": {},
        "correctness_defect": {},
        "rejected_experiment": {
            "candidate": "chunk_cues cached overlap line sizes",
            "decision": "rejected: default-overlap runs showed no stable speed gain and increased peak memory; a threshold or duplicated code path was judged unnecessary complexity",
            "observed_default_and_large_overlap_run": {
                "production_defaults_160_cues_ms": {"baseline": 0.574, "candidate": 0.687},
                "production_defaults_500_cues_ms": {"baseline": 1.749, "candidate": 2.074},
                "production_defaults_peak_bytes_500_cues": {"baseline": 9612, "candidate": 9612},
                "expanded_overlap_40_500_cues_ms": {"baseline": 2.578, "candidate": 2.276},
                "expanded_overlap_40_500_cues_peak_bytes": {"baseline": 13888, "candidate": 14496},
            },
        },
    }

    with tempfile.TemporaryDirectory(prefix="compute-ablation-") as temporary:
        baseline, baseline_path = baseline_module(Path(temporary))
        for label, char_count in (("short", 100), ("ordinary", 10_000), ("very_long", 1_000_000)):
            text = "数" * char_count
            base_cue = baseline.Cue(0, max(1, char_count), text, "synthetic-fixed-seed")
            current_cue = current.Cue(base_cue.start_ms, base_cue.end_ms, base_cue.text, base_cue.cue_id)
            expected = measure(baseline._split_long_cue, base_cue)
            actual = measure(current._split_long_cue, current_cue)
            equal = expected["parts"] == actual["parts"]
            chunk_count = len(expected["parts"])
            full_slice_count = max(0, chunk_count - 1)
            baseline_copied_chars = (
                0 if char_count <= LIMIT else
                full_slice_count * LIMIT + sum(max(0, char_count - index * LIMIT) for index in range(1, chunk_count))
            )
            current_copied_chars = 0 if char_count <= LIMIT else char_count
            results["long_cue_scenarios"][label] = {
                "input_chars": char_count,
                "output_equal": equal,
                "output_parts": chunk_count,
                "baseline": {key: value for key, value in expected.items() if key != "parts"},
                "optimized": {key: value for key, value in actual.items() if key != "parts"},
                "estimated_slice_copied_chars": {
                    "baseline": baseline_copied_chars,
                    "optimized": current_copied_chars,
                },
            }

        # The old public validation allowed a zero split limit; prove the old
        # behavior only in a child process so a hang cannot stall this script.
        code = (
            "import baseline_transcript_pipeline as m; "
            "m.chunk_cues([m.Cue(0, 10, 'x' * 300, 'long')], "
            "target_chars=50, max_chars=120)"
        )
        try:
            subprocess.run([sys.executable, "-c", code], cwd=temporary, timeout=1, check=True)
            old_result = "returned"
        except subprocess.TimeoutExpired:
            old_result = "timed_out_after_1s"
        except subprocess.CalledProcessError as error:
            old_result = f"exited_{error.returncode}"
        try:
            current.chunk_cues([current.Cue(0, 10, "x" * 300, "long")], target_chars=50, max_chars=120)
            current_result = "returned"
        except ValueError:
            current_result = "ValueError"
        results["correctness_defect"] = {
            "case": "non-empty cue with max_chars=120 (split limit becomes zero)",
            "baseline_child_process": old_result,
            "current": current_result,
            "fixed_by_rejecting_max_chars_at_or_below_120": current_result == "ValueError",
        }

    results["acceptance"] = {
        "all_long_cue_outputs_equal": all(
            row["output_equal"] for row in results["long_cue_scenarios"].values()
        ),
        "defect_guard_confirmed": results["correctness_defect"]["fixed_by_rejecting_max_chars_at_or_below_120"],
    }
    very_long = results["long_cue_scenarios"]["very_long"]
    results["optimization_decision"] = (
        "keep for very long single cues only: outputs match, short/ordinary peak memory is unchanged, and the 1M-character case improves CPU and peak memory; no ordinary-input or whole-app speed claim"
        if results["acceptance"]["all_long_cue_outputs_equal"]
        and very_long["optimized"]["median_ms"] < very_long["baseline"]["median_ms"]
        and very_long["optimized"]["peak_memory_bytes_median"] < very_long["baseline"]["peak_memory_bytes_median"]
        and all(row["optimized"]["peak_memory_bytes_median"] <= row["baseline"]["peak_memory_bytes_median"] for row in results["long_cue_scenarios"].values())
        else "reject: a measured gate failed"
    )
    output_path = ROOT / "docs/main-audit-2026-10-24/compute-results.json"
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "acceptance": results["acceptance"]}, ensure_ascii=False, indent=2))
    if not all(row["output_equal"] for row in results["long_cue_scenarios"].values()):
        raise SystemExit("baseline and optimized long-cue outputs differ")


if __name__ == "__main__":
    main()
