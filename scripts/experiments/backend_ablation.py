#!/usr/bin/env python3
"""Compare calendar overview's two-read baseline with its bounded merge variant.

This uses the real AcademicFeatureService event normalization and range filtering,
with a deterministic in-memory Canvas endpoint. It measures client/API-call behavior;
it does not model real network latency or Canvas server performance.
"""
from __future__ import annotations

import hashlib
import json
import random
import statistics
import sys
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sjtu_learning_assistant.academic_features import (  # noqa: E402
    AcademicFeatureService,
    _event_time,
)

SEED = 20261024
REPEATS = 15
COURSE_IDS = [str(value) for value in range(1, 11)]
BASELINE_REVISION = "3aa54d1a76fd8603dcc43eef5629eb5ae3cc37dd"


class SyntheticCanvas:
    def __init__(self, events: list[dict[str, Any]], *, response_cache: bool) -> None:
        self.events = events
        self.response_cache = response_cache
        self.cache: dict[tuple[tuple[str, ...], str, str], list[dict[str, Any]]] = {}
        self.calls = 0
        self.remote_calls = 0
        self.cache_hits = 0

    def calendar_events(self, contexts: list[str], start: str, end: str):
        self.calls += 1
        key = (tuple(contexts), start, end)
        if self.response_cache and key in self.cache:
            self.cache_hits += 1
            return [dict(item) for item in self.cache[key]]
        self.remote_calls += 1
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        selected = [
            item
            for item in self.events
            if item["context_code"] in contexts
            and start_dt <= datetime.fromisoformat(item["start_at"]) < end_dt
        ]
        if self.response_cache:
            self.cache[key] = [dict(item) for item in selected]
        return [dict(item) for item in selected]


def make_events(count: int) -> list[dict[str, Any]]:
    rng = random.Random(SEED + count)
    origin = datetime(2026, 9, 1, tzinfo=timezone.utc)
    events = []
    for index in range(count):
        when = origin + timedelta(days=rng.randrange(150), minutes=rng.randrange(1440))
        course = index % len(COURSE_IDS)
        events.append(
            {
                "id": index + 1,
                "title": f"Synthetic assignment {index + 1}",
                "context_code": f"course_{COURSE_IDS[course]}",
                "start_at": when.isoformat(),
                "assignment": {"id": index + 1},
            }
        )
    return events


def baseline(
    service: AcademicFeatureService, now: datetime, *, distant: bool
) -> dict[str, Any]:
    result = service.calendar_overview(2026, 9, now=now, course_ids=COURSE_IDS)
    return {
        "month_events": list(result.month_events),
        "upcoming_events": list(result.upcoming_events),
    }


def optimized(
    service: AcademicFeatureService, now: datetime, *, distant: bool
) -> dict[str, Any]:
    month_start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    month_end = datetime(2026, 10, 1, tzinfo=timezone.utc)
    upcoming_end = now + timedelta(days=31)
    if not (
        isinstance(COURSE_IDS, Sequence)
        and max(month_start, now) <= min(month_end, upcoming_end)
    ):
        return baseline(service, now, distant=distant)

    combined = service.events_between(
        min(month_start, now),
        max(month_end, upcoming_end),
        course_ids=COURSE_IDS,
    )

    def in_interval(start: datetime, end: datetime):
        return [
            event
            for event in combined
            if (moment := _event_time(event)) is None or start <= moment < end
        ]

    return {
        "month_events": in_interval(month_start, month_end),
        "upcoming_events": in_interval(now, upcoming_end),
    }


def dedup_counterexample() -> dict[str, Any]:
    events = [
        {
            "id": "month-instance",
            "title": "Homework September snapshot",
            "context_code": "course_1",
            "start_at": "2026-09-10T09:00:00+00:00",
            "assignment": {"id": 77},
        },
        {
            "id": "upcoming-instance",
            "title": "Homework October snapshot",
            "context_code": "course_1",
            "start_at": "2026-10-10T09:00:00+00:00",
            "assignment": {"id": 77},
        },
    ]
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    summary = {}
    for name, variant in (("baseline", baseline), ("merged_candidate", optimized)):
        canvas = SyntheticCanvas(events, response_cache=False)
        with TemporaryDirectory(prefix="backend-calendar-counterexample-") as temp_dir:
            service = AcademicFeatureService(canvas, export_root=temp_dir)
            value = variant(service, now, distant=False)
        summary[name] = {
            "calls": canvas.calls,
            "month_events": value["month_events"],
            "upcoming_events": value["upcoming_events"],
        }
    return {
        "same_event_identity": ["assignment", "77", ""],
        "baseline": summary["baseline"],
        "merged_candidate": summary["merged_candidate"],
        "outputs_identical": digest(summary["baseline"])
        == digest(summary["merged_candidate"]),
    }


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_variant(
    events: list[dict[str, Any]],
    now: datetime,
    variant: Callable[..., dict[str, Any]],
    *,
    cached: bool,
) -> dict[str, Any]:
    canvas = SyntheticCanvas(events, response_cache=cached)
    with TemporaryDirectory(prefix="backend-calendar-ablation-") as temp_dir:
        service = AcademicFeatureService(canvas, export_root=temp_dir)
        if cached:
            variant(service, now, distant=now.month != 9)  # warm mock response cache
            canvas.calls = canvas.remote_calls = canvas.cache_hits = 0
        timings: list[float] = []
        peak_bytes = 0
        result = None
        for _ in range(REPEATS):
            tracemalloc.start()
            started = time.perf_counter()
            result = variant(service, now, distant=now.month != 9)
            timings.append((time.perf_counter() - started) * 1000)
            _current_bytes, iteration_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peak_bytes = max(peak_bytes, iteration_peak)
        return {
            "result_sha256": digest(result),
            "median_ms": round(statistics.median(timings), 4),
            "peak_tracemalloc_bytes": peak_bytes,
            "client_calls_total": canvas.calls,
            "remote_calls_total": canvas.remote_calls,
            "cache_hits_total": canvas.cache_hits,
            "calls_per_operation": canvas.calls / REPEATS,
            "remote_calls_per_operation": canvas.remote_calls / REPEATS,
        }


def main() -> None:
    rows = []
    for count in (100, 1000, 5000):
        events = make_events(count)
        for label, now in (
            ("overlap", datetime(2026, 9, 15, tzinfo=timezone.utc)),
            ("distant", datetime(2026, 12, 15, tzinfo=timezone.utc)),
        ):
            for cache_label, cached in (("cold_cache_off", False), ("warm_mock_cache", True)):
                baseline_result = run_variant(events, now, baseline, cached=cached)
                optimized_result = run_variant(events, now, optimized, cached=cached)
                rows.append(
                    {
                        "event_count": count,
                        "interval_relation": label,
                        "cache_state": cache_label,
                        "outputs_identical": baseline_result["result_sha256"]
                        == optimized_result["result_sha256"],
                        "baseline": baseline_result,
                        "optimized": optimized_result,
                    }
                )

    output = {
        "method": {
            "baseline_revision": BASELINE_REVISION,
            "seed": SEED,
            "repeats_per_variant": REPEATS,
            "event_counts": [100, 1000, 5000],
            "intervals": {
                "overlap": "September 2026 month and Sep 15 + 31 days",
                "distant": "September 2026 month and Dec 15 + 31 days",
            },
            "baseline": "Current restored production AcademicFeatureService.calendar_overview, matching baseline_revision.",
            "optimized": "Rejected overlap/touch-only candidate: stable explicit course Sequence required; distant ranges call the production baseline unchanged; merged ranges use production events_between filtering and deduplication.",
            "cold_cache_off": "Mock response cache disabled; repeated service calls still use deterministic in-memory event fixtures.",
            "warm_mock_cache": "Mock response cache primed once, then timed calls use cached endpoint responses.",
            "measurements": "Median wall time over 15 operations and maximum per-operation tracemalloc peak; API call counts are mock client/server call counts.",
            "limits": "No SQLite or real Canvas/network is involved. Timings and memory describe local Python work only, not network latency or server performance.",
        },
        "results": rows,
        "counterexample": dedup_counterexample(),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
