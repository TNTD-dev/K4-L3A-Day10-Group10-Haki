from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from core.config import Settings, load_settings
from core.utils import now_utc, read_json, write_csv, write_json
from ingestion.corruption import corrupt_clean_dataframe
from observability.quality import (
    build_freshness_report,
    data_fingerprint,
    enforce_quality_gate,
    freshness_report_path,
    run_data_quality_checks,
)
from observability.reporting import comparison_rows, generate_corruption_report


def save_dataframe(df: pd.DataFrame, csv_path: Path, json_path: Path) -> None:
    write_csv(df, csv_path)
    write_json(json_path, json.loads(df.to_json(orient="records")))


def load_clean_dataframe(path: Path) -> pd.DataFrame:
    return pd.read_json(path, convert_dates=False, dtype={"paper_id": str, "published": str})


def repair_from_raw(settings: Settings) -> pd.DataFrame:
    """Idempotent repair: luon dung lai tu raw snapshot bat bien, khong va du lieu hong."""
    from ingestion.cleaning import rebuild_clean_dataframe_from_raw

    return rebuild_clean_dataframe_from_raw(settings.paths.raw_records_json, now_utc())


def evaluate(settings: Settings, df: pd.DataFrame, embeddings_path: Path, metrics_path: Path, answers_path: Path) -> dict:
    from evaluation.metrics import evaluate_pipeline
    from retrieval.index import LocalEmbeddingIndex

    index = LocalEmbeddingIndex.build(df, settings, embeddings_path)
    return evaluate_pipeline(settings, index, settings.paths.eval_testset, metrics_path, answers_path).summary


def observe(df: pd.DataFrame, settings: Settings, name: str, reference_unique_ids: int | None = None) -> tuple[dict, dict]:
    quality = run_data_quality_checks(df, settings, name, reference_unique_ids)
    freshness = build_freshness_report(df, settings, freshness_report_path(settings, name))
    return quality, freshness


def print_comparison(baseline: dict, corrupted: dict, repaired: dict) -> None:
    header = f"{'Metric':<24}{'Baseline':>10}{'Corrupted':>11}{'Repaired':>10}"
    print("\n" + header + "\n" + "-" * len(header))
    for label, b, c, r, *_ in comparison_rows(baseline, corrupted, repaired):
        print(f"{label:<24}{b if b is not None else float('nan'):>10.4f}{c if c is not None else float('nan'):>11.4f}{r if r is not None else float('nan'):>10.4f}")


def sanity_check(baseline: dict, corrupted: dict, repaired: dict) -> list[str]:
    """Chi canh bao, KHONG sua so lieu: ket qua nao cung phai la so that tu pipeline."""
    from observability.reporting import is_recovered

    warnings = []
    if corrupted.get("mean_token_f1", 0) >= baseline.get("mean_token_f1", 0):
        warnings.append("Corrupted F1 khong giam so voi baseline - kiem tra corruption co cham benchmark khong.")
    if not is_recovered(baseline, repaired):
        warnings.append("Repaired chua khop baseline - kiem tra repair/cleaning co idempotent khong.")
    return warnings


def main(settings: Settings | None = None, evaluate_fn=evaluate, repair_fn=repair_from_raw) -> dict:
    """Corruption -> Quality Gate -> evaluate -> (auto) repair -> evaluate -> compare.

    `evaluate_fn` / `repair_fn` inject duoc de test observability tren data gia truoc khi merge RAG.
    """
    settings = settings or load_settings()
    paths = settings.paths
    if not paths.baseline_metrics.exists():
        raise FileNotFoundError("Chua co baseline_metrics.json - chay `python script/run_phase1.py` truoc.")
    baseline_metrics = read_json(paths.baseline_metrics)
    clean_df = load_clean_dataframe(paths.clean_json)

    # 1. Corrupt + observe
    # Targeted injection: loi nham vao paper trong benchmark de tac dong do duoc (ghi ro trong log/report).
    corrupted_df = corrupt_clean_dataframe(clean_df, paths.corruption_log, test_set=read_json(paths.eval_testset))
    save_dataframe(corrupted_df, paths.corrupted_clean_csv, paths.corrupted_clean_json)
    reference_ids = int(clean_df["paper_id"].nunique())  # volume tham chieu = baseline
    corrupted_quality, corrupted_freshness = observe(corrupted_df, settings, "corrupted", reference_ids)
    print(f"[gate] corrupted quality={corrupted_quality['success']} failed={corrupted_quality['failed_expectations']}")
    print(f"[gate] corrupted freshness is_fresh={corrupted_freshness['is_fresh']} stale_ratio={corrupted_freshness['stale_ratio']}")

    # 2. Audit mode (co chu dich): KHONG enforce gate, van index data hong de do silent failure.
    #    Production path (repair ben duoi) thi enforce.
    corrupted_metrics = evaluate_fn(settings, corrupted_df, paths.corrupted_embeddings_json, paths.corrupted_metrics, paths.corrupted_answers)

    # 3. Auto-repair: gate fail -> tu dong rebuild tu raw
    reasons = list(corrupted_quality["failed_expectations"])
    if not corrupted_freshness["is_fresh"]:
        reasons.append("freshness_sla")
    auto_repair = {"triggered": bool(reasons), "trigger_reasons": reasons, "source": str(paths.raw_records_json.name)}
    if not reasons:
        print("[repair] gate PASS - khong can repair, van chay repair de doi chieu.")
    repaired_df = repair_fn(settings)
    save_dataframe(repaired_df, paths.repaired_clean_csv, paths.repaired_clean_json)
    repaired_quality, repaired_freshness = observe(repaired_df, settings, "repaired", reference_ids)
    auto_repair["repaired_gate_pass"] = bool(repaired_quality["success"])
    # Idempotency/lineage: noi dung repaired phai trung baseline (bo age_days).
    auto_repair["baseline_fingerprint"] = data_fingerprint(clean_df)
    auto_repair["repaired_fingerprint"] = repaired_quality["data_fingerprint"]
    auto_repair["fingerprint_match"] = auto_repair["baseline_fingerprint"] == auto_repair["repaired_fingerprint"]
    enforce_quality_gate(repaired_quality)  # repair ma van ban -> dung, khong serve
    repaired_metrics = evaluate_fn(settings, repaired_df, paths.repaired_embeddings_json, paths.repaired_metrics, paths.repaired_answers)

    # 4. Report
    corruption_log = read_json(paths.corruption_log)
    generate_corruption_report(
        paths.comparison_report,
        baseline_metrics, corrupted_metrics, repaired_metrics,
        corrupted_quality, repaired_quality,
        corrupted_freshness, repaired_freshness,
        corruption_log=corruption_log, auto_repair=auto_repair,
        answers_by_state={
            state: read_json(path)
            for state, path in [("baseline", paths.baseline_answers), ("corrupted", paths.corrupted_answers), ("repaired", paths.repaired_answers)]
            if path.exists()
        },
    )
    print_comparison(baseline_metrics, corrupted_metrics, repaired_metrics)
    for warning in sanity_check(baseline_metrics, corrupted_metrics, repaired_metrics):
        print(f"[WARN] {warning}")
    print(f"\nReport: {paths.comparison_report}")

    try:
        from observability.dashboard import build_dashboard

        print(f"Dashboard: {build_dashboard(settings)}")
    except Exception as exc:  # dashboard la bonus, khong duoc lam hong flow chinh
        print(f"[dashboard] skipped: {exc}")

    return {"baseline": baseline_metrics, "corrupted": corrupted_metrics, "repaired": repaired_metrics, "auto_repair": auto_repair}
