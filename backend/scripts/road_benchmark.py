"""Offline comparison of the three road recovery strategies on fixed inputs."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import pairwise
from math import ceil
from pathlib import Path
from time import perf_counter

from app.seed import benchmark_snapshot
from app.services.road_recovery import ORIGIN, AvoidArea, evaluate_road_candidates

STRATEGIES = ("WAIT", "DETOUR", "RESEQUENCE")
SCENARIOS = {
    "LIGHT_DELAY": (0, 5, 10, 15),
    "HEAVY_DELAY": (25, 35, 45, 60),
    "NO_BENEFIT": (0, 2, 4, 6),
    "NO_DETOUR": (8, 16, 24, 32),
    "RESEQUENCE_REQUIRED": (17, 23, 29, 36),
    "HARD_WINDOWS": (10, 16, 25, 40),
    "NO_ROUTE": (5, 15, 25, 35),
}
CASE_COLUMNS = (
    "data_source",
    "case_id",
    "scenario",
    "strategy",
    "reported_wait_minutes",
    "available",
    "recommended",
    "late_orders",
    "total_delay_minutes",
    "distance_m",
    "extra_distance_m",
    "changed_orders",
    "violations",
    "unavailable_reason",
    "solver_case_ms",
)


def leg(minutes, distance_m):
    return {"duration_seconds": minutes * 60, "distance_m": distance_m, "geometry": None}


def synthetic_cases():
    for scenario, delays in SCENARIOS.items():
        for index, delay in enumerate(delays):
            case_id = f"{scenario}-{index + 1:02d}"
            direct = dict(
                zip(
                    pairwise((ORIGIN, "A", "B", "C")),
                    (
                        leg(8, 1000),
                        leg(6, 900),
                        leg(5, 800),
                    ),
                )
            )
            avoided = {
                (a, b): leg(10 + index, 1600)
                for a in (ORIGIN, "A", "B", "C")
                for b in "ABC"
                if a != b
            }
            payload = benchmark_snapshot()
            if scenario == "NO_BENEFIT":
                avoided = {pair: leg(9 + index, 1500) for pair in avoided}
            elif scenario == "NO_DETOUR":
                avoided = {}
            elif scenario == "RESEQUENCE_REQUIRED":
                avoided = {
                    (ORIGIN, "B"): leg(5 + index, 950),
                    ("B", "A"): leg(6, 1000),
                    ("A", "C"): leg(7, 1200),
                }
            elif scenario == "HARD_WINDOWS":
                clock = datetime.fromisoformat(payload["clock_time"])
                for order, deadline in zip(payload["orders"], (18, 16)):
                    order["window_type"] = "HARD"
                    order["latest_arrival"] = (clock + timedelta(minutes=deadline)).isoformat()
                avoided.update(
                    {
                        (ORIGIN, "A"): leg(7, 1250),
                        ("A", "B"): leg(8, 1400),
                        (ORIGIN, "B"): leg(4 + index, 800),
                        ("B", "A"): leg(4, 850),
                    }
                )
            elif scenario == "NO_ROUTE":
                if index == 3:
                    direct = {}
                else:
                    direct.pop(list(direct)[index])
                # The last stop cannot be reached by a verified detour.
                avoided = {pair: cost for pair, cost in avoided.items() if pair[1] != "C"}
            yield "SYNTHETIC", case_id, scenario, delay, payload, direct, avoided


def recorded_case(path):
    record = json.loads(path.read_text(encoding="utf-8"))
    delay = record["delay_minutes"]
    if type(delay) is not int or not 0 <= delay <= 1440:
        raise ValueError("Recorded wait time must be an integer between 0 and 1440")

    def edges(items):
        costs = {}
        for item in items:
            pair = (item["from"], item["to"])
            if (
                pair[0] not in (ORIGIN, "A", "B", "C")
                or pair[1] not in ("A", "B", "C")
                or pair in costs
            ):
                raise ValueError("Unexpected or duplicate recorded road edge")
            cost = {key: item[key] for key in ("duration_seconds", "distance_m")}
            if any(type(value) is not int or value < 0 for value in cost.values()):
                raise ValueError("Recorded road costs must be nonnegative integers")
            costs[pair] = {**cost, "geometry": None}
        return costs

    return (
        "AMAP_COST_REPLAY",
        record["case_id"],
        "WUHAN_SIMULATED_INCIDENT",
        delay,
        benchmark_snapshot(),
        edges(record["direct"]),
        edges(record["avoided"]),
    ), record["recorded_at"]


def summarize(rows, durations):
    cases = defaultdict(dict)
    for row in rows:
        cases[row["case_id"]][row["strategy"]] = row
    recommendations = Counter()
    comparable = improved = no_feasible = 0
    for strategies in cases.values():
        chosen = next((item for item in strategies.values() if item["recommended"]), None)
        if chosen is None:
            no_feasible += 1
            continue
        recommendations[chosen["strategy"]] += 1
        baseline = strategies["WAIT"]
        if baseline["available"]:
            comparable += 1
            if (
                chosen["late_orders"] < baseline["late_orders"]
                or chosen["total_delay_minutes"] < baseline["total_delay_minutes"]
            ):
                improved += 1
    samples = sorted(durations)
    return {
        "cases": len(cases),
        "no_feasible_cases": no_feasible,
        "comparable_to_feasible_wait": comparable,
        "lower_late_orders_or_minutes": improved,
        "recommendations": {strategy: recommendations[strategy] for strategy in STRATEGIES},
        "solver_case_ms_p50": round(samples[ceil(len(samples) * 0.50) - 1], 3),
        "solver_case_ms_p95": round(samples[ceil(len(samples) * 0.95) - 1], 3),
    }


def benchmark(replay_path=None):
    cases = list(synthetic_cases())
    replay_time = None
    if replay_path:
        replay, replay_time = recorded_case(replay_path)
        cases.insert(0, replay)
    rows, durations = [], defaultdict(list)
    for source, case_id, scenario, delay, payload, direct, avoided in cases:
        event = {
            "incident_id": case_id,
            "event_type": "ROAD_DELAY",
            "vehicle_id": "V01",
            "version": 1,
            "delay_minutes": delay,
        }
        started = perf_counter()
        evaluation = evaluate_road_candidates(
            payload,
            event,
            [0.0, 0.0],
            direct,
            avoided,
            AvoidArea(-0.01, -0.01, 0.01, 0.01),
        )
        elapsed = (perf_counter() - started) * 1000
        durations[source].append(elapsed)
        result = evaluation["road_analysis"]["road_comparison"]
        for variant in result["variants"]:
            metrics = variant.get("metrics", {})
            rows.append(
                {
                    "data_source": source,
                    "case_id": case_id,
                    "scenario": scenario,
                    "strategy": variant["strategy"],
                    "reported_wait_minutes": delay,
                    "available": variant["available"],
                    "recommended": variant["strategy"] == result["recommended_strategy"],
                    "late_orders": metrics.get("late_order_count"),
                    "total_delay_minutes": metrics.get("total_delay_minutes"),
                    "distance_m": metrics.get("total_distance_m"),
                    "extra_distance_m": metrics.get("extra_distance_m"),
                    "changed_orders": metrics.get("changed_order_count"),
                    "violations": ";".join(
                        sorted(
                            {
                                v["code"]
                                for v in variant.get("constraints", {}).get("violations", [])
                            }
                        )
                    ),
                    "unavailable_reason": variant.get("reason"),
                    "solver_case_ms": round(elapsed, 3),
                }
            )
    return rows, {
        "method": "One fixed order set and observation clock per case. WAIT adds the case's incremental hold once to baseline leg times. DETOUR and RESEQUENCE use supplied eligible road edges.",
        "timing": "Wall-clock solver time per entire case. Excludes HTTP, queue, and approval time.",
        "data_sources": {
            "SYNTHETIC": "Constructed cases, not operational statistics.",
            "AMAP_COST_REPLAY": "Captured Wuhan road costs with simulated orders and congestion. Geometry and live calls are not repeated.",
        },
        "recorded_wuhan_at": replay_time,
        "by_source": {
            source: summarize([row for row in rows if row["data_source"] == source], times)
            for source, times in durations.items()
        },
    }


def write_reports(output, rows, summary):
    output.mkdir(parents=True, exist_ok=True)
    csv_file = output / "road-benchmark.csv"
    with csv_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CASE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    json_file = output / "road-benchmark-summary.json"
    json_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return csv_file, json_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recorded-case", type=Path)
    args = parser.parse_args()
    rows, summary = benchmark(args.recorded_case)
    write_reports(args.output, rows, summary)
    print(json.dumps({"variants": len(rows), **summary["by_source"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
