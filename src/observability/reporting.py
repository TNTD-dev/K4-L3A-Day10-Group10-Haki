from __future__ import annotations

from typing import Any

from core.utils import now_utc, write_text

METRIC_LABELS = [
    ("retrieval_hit_rate", "Retrieval Hit Rate"),
    ("mean_token_f1", "Mean Token F1"),
    ("judge_accuracy", "LLM Judge Accuracy"),
    ("mean_judge_score", "Mean Judge Score (1-5)"),
]
RECOVERY_TOLERANCE = 0.01


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "✅ PASS" if value else "❌ FAIL"
    if isinstance(value, float):
        return f"{value:.4f}"
    return "—" if value is None else str(value)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_fmt(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _quality_rows(quality: dict[str, Any]) -> list[list[Any]]:
    return [
        [r.get("pillar", ""), r["expectation"], r.get("column") or "table", r.get("severity", "critical"), r["success"], r.get("unexpected_count", 0)]
        for r in quality.get("results", [])
    ]


QUALITY_HEADERS = ["Pillar", "Expectation", "Column", "Severity", "Result", "Unexpected"]


def _freshness_rows(freshness: dict[str, Any]) -> list[list[Any]]:
    keys = ["total_rows", "stale_rows", "stale_ratio", "latest_published", "oldest_published", "latest_age_days", "is_fresh"]
    return [[k, freshness.get(k)] for k in keys]


def generate_phase1_report(
    report_path,
    source_summary: dict[str, Any],
    metrics: dict[str, Any],
    quality: dict[str, Any],
    freshness: dict[str, Any],
) -> None:
    parts = [
        "# Phase 1 Report — Baseline Pipeline",
        f"_Generated: {now_utc().isoformat()}_",
        "## 1. Source & Lineage",
        _table(["Field", "Value"], [[k, v] for k, v in source_summary.items()]),
        "## 2. RAG Baseline Metrics",
        _table(["Metric", "Value"], [[label, metrics.get(key)] for key, label in METRIC_LABELS] + [["Samples", metrics.get("samples")]]),
        f"## 3. Data Quality Gate ({quality.get('engine', 'Great Expectations')})",
        f"**Gate:** {_fmt(quality.get('success'))} — {quality.get('evaluated_expectations', 0)} expectations, "
        f"critical failed: {', '.join(quality.get('failed_expectations', [])) or 'none'}, "
        f"warnings: {', '.join(quality.get('warnings', [])) or 'none'} · fingerprint `{quality.get('data_fingerprint')}`",
        _table(QUALITY_HEADERS, _quality_rows(quality)),
        f"## 4. Freshness SLA (age_days > {freshness.get('threshold_days')} ≤ {freshness.get('max_stale_ratio', 0.25):.0%} rows)",
        _table(["Field", "Value"], _freshness_rows(freshness)),
    ]
    write_text(report_path, "\n\n".join(parts) + "\n")


def _delta(base: Any, other: Any) -> str:
    if isinstance(base, (int, float)) and isinstance(other, (int, float)):
        return f"{other - base:+.4f}"
    return "—"


def comparison_rows(baseline: dict, corrupted: dict, repaired: dict) -> list[list[Any]]:
    return [
        [label, baseline.get(k), corrupted.get(k), repaired.get(k), _delta(baseline.get(k), corrupted.get(k)), _delta(baseline.get(k), repaired.get(k))]
        for k, label in METRIC_LABELS
    ]


def is_recovered(baseline: dict, repaired: dict) -> bool:
    return all(
        isinstance(baseline.get(k), (int, float))
        and isinstance(repaired.get(k), (int, float))
        and abs(baseline[k] - repaired[k]) <= RECOVERY_TOLERANCE
        for k, _ in METRIC_LABELS
    )


def _detected(corruption: dict, quality: dict, freshness: dict) -> bool:
    failed = " ".join(quality.get("failed_expectations", []))
    detectors = {
        "blank_summary": "expect_column_value_lengths_to_be_between(summary)" in failed,
        "truncate_title": "expect_column_value_lengths_to_be_between(title)" in failed,
        "duplicate_rows": "expect_column_values_to_be_unique(paper_id)" in failed,
        "drop_latest_records": "expect_column_unique_value_count_to_be_between(paper_id)" in failed,  # volume
        "stale_date": not freshness.get("is_fresh", True),
    }
    return detectors.get(corruption["name"], False)  # inject_noise: khong co detector cau truc (silent)


def _per_type_table(answers_by_state: dict[str, list[dict[str, Any]]]) -> str:
    states = list(answers_by_state)
    types = sorted({a["question_type"] for answers in answers_by_state.values() for a in answers})
    rows = []
    for kind in types:
        row: list[Any] = [kind]
        for state in states:
            items = [a for a in answers_by_state[state] if a["question_type"] == kind]
            hit = sum(1 for a in items if a["retrieval_hit"]) / len(items) if items else 0.0
            f1 = sum(a["token_f1"] for a in items) / len(items) if items else 0.0
            row.append(f"{hit:.2f} / {f1:.2f}")
        rows.append(row)
    return _table(["Question type"] + [s.capitalize() for s in states], rows)


def generate_corruption_report(
    report_path,
    baseline_metrics: dict[str, Any],
    corrupted_metrics: dict[str, Any],
    repaired_metrics: dict[str, Any],
    corrupted_quality: dict[str, Any],
    repaired_quality: dict[str, Any],
    corrupted_freshness: dict[str, Any],
    repaired_freshness: dict[str, Any],
    corruption_log: dict[str, Any] | None = None,
    auto_repair: dict[str, Any] | None = None,
    answers_by_state: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    recovered = is_recovered(baseline_metrics, repaired_metrics)
    parts = [
        "# Corruption & Repair Report — Baseline vs Corrupted vs Repaired",
        f"_Generated: {now_utc().isoformat()}_",
        "## 1. RAG Metrics (3 trạng thái)",
        _table(["Metric", "Baseline", "Corrupted", "Repaired", "Δ Corrupted", "Δ Repaired"],
               comparison_rows(baseline_metrics, corrupted_metrics, repaired_metrics)),
        f"**Recovery verdict:** {'✅ Repaired khớp Baseline' if recovered else '⚠️ Repaired CHƯA khớp Baseline'} "
        f"(tolerance ±{RECOVERY_TOLERANCE}).",
        "## 2. Data Quality Gate (GX 1.x)",
        _table(["", "Corrupted", "Repaired"], [
            ["Overall", corrupted_quality.get("success"), repaired_quality.get("success")],
            ["Rows / unique papers", f"{corrupted_quality.get('row_count')} / {corrupted_quality.get('unique_papers')}",
             f"{repaired_quality.get('row_count')} / {repaired_quality.get('unique_papers')}"],
            ["Critical failed", ", ".join(corrupted_quality.get("failed_expectations", [])) or "none",
             ", ".join(repaired_quality.get("failed_expectations", [])) or "none"],
            ["Warnings", ", ".join(corrupted_quality.get("warnings", [])) or "none",
             ", ".join(repaired_quality.get("warnings", [])) or "none"],
            ["Data fingerprint", corrupted_quality.get("data_fingerprint"), repaired_quality.get("data_fingerprint")],
        ]),
        "### Chi tiết expectation (Corrupted)",
        _table(QUALITY_HEADERS, _quality_rows(corrupted_quality)),
        "## 3. Freshness SLA",
        _table(["Field", "Corrupted", "Repaired"], [
            [k, corrupted_freshness.get(k), repaired_freshness.get(k)]
            for k in ["total_rows", "stale_rows", "stale_ratio", "latest_published", "latest_age_days", "is_fresh"]
        ]),
    ]

    if corruption_log:
        rows = [
            [c["name"], len(c["affected_paper_ids"]), len(c.get("benchmark_hits", [])), c["expected_detector"],
             "✅ detected" if _detected(c, corrupted_quality, corrupted_freshness) else "⚠️ silent"]
            for c in corruption_log.get("scenarios", [])
        ]
        parts += [
            f"## 4. Corruption Impact ({corruption_log.get('input_rows')} → {corruption_log.get('output_rows')} rows)",
            _table(["Corruption", "Rows", "Benchmark papers hit", "Detector", "Observed"], rows),
        ]
        if corruption_log.get("targeted_injection"):
            parts.append("_Targeted injection: lỗi được nhắm vào các paper có trong test set để tác động đo được; "
                         "danh sách paper bị ảnh hưởng nằm trong `corruption_log.json`._")

    if answers_by_state:
        parts += ["## 4b. Metrics theo loại câu hỏi (hit rate / token F1)", _per_type_table(answers_by_state)]

    if auto_repair:
        parts += [
            "## 5. Auto-Repair",
            _table(["Field", "Value"], [
                [k, ("yes" if v else "no") if isinstance(v, bool) else ", ".join(map(str, v)) if isinstance(v, list) else v]
                for k, v in auto_repair.items()
            ]),
        ]

    silent = [c["name"] for c in (corruption_log or {}).get("scenarios", []) if not _detected(c, corrupted_quality, corrupted_freshness)]
    parts += [
        "## Analysis",
        f"- Quality Gate {'chặn được' if not corrupted_quality.get('success') else 'KHÔNG chặn'} bộ dữ liệu corrupted; "
        f"Freshness SLA: {'vi phạm' if not corrupted_freshness.get('is_fresh') else 'đạt'}.",
        f"- Lỗi lọt qua kiểm tra cấu trúc (silent): {', '.join(silent) or 'không có'} → chỉ lộ ra qua RAG metrics.",
        "- Repair dựng lại từ raw snapshot bất biến (idempotent): chạy N lần cho cùng kết quả, không vá tay dữ liệu hỏng.",
    ]
    write_text(report_path, "\n\n".join(parts) + "\n")
