from scripts.road_benchmark import benchmark, write_reports


def test_benchmark_exports_distinct_outcomes(tmp_path):
    rows, summary = benchmark()
    groups = {(row["case_id"], row["strategy"]): row for row in rows}
    assert len(rows) == 84 and len(groups) == 84
    assert all(row["data_source"] == "SYNTHETIC" for row in rows)
    assert groups["NO_BENEFIT-01", "WAIT"]["recommended"] is True
    assert groups["NO_BENEFIT-01", "DETOUR"]["extra_distance_m"] > 0
    assert groups["NO_BENEFIT-01", "WAIT"]["late_orders"] == 0
    assert groups["NO_BENEFIT-01", "DETOUR"]["late_orders"] == 0
    assert not any(
        groups["NO_ROUTE-01", strategy]["available"]
        for strategy in ("WAIT", "DETOUR", "RESEQUENCE")
    )
    assert groups["RESEQUENCE_REQUIRED-01", "DETOUR"]["available"] is False
    assert groups["RESEQUENCE_REQUIRED-01", "RESEQUENCE"]["recommended"] is True
    assert summary["by_source"]["SYNTHETIC"]["no_feasible_cases"] >= 4
    csv_file, json_file = write_reports(tmp_path, rows, summary)
    assert csv_file.read_text(encoding="utf-8-sig").count("\n") == 85
    assert json_file.is_file()
