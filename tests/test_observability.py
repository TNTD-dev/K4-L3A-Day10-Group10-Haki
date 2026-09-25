"""Test Observability tren raw snapshot that (data/raw/crossref_records.json). Chay: python -m pytest tests -q"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from core.config import load_settings
from core.utils import read_json
from evaluation.metrics import _token_f1
from evaluation.testset import build_test_set
from ingestion.cleaning import rebuild_clean_dataframe_from_raw
from ingestion.corruption import corrupt_clean_dataframe, inject_noise
from observability.dashboard import build_dashboard
from observability.quality import (
    REQUIRED_COLUMNS,
    DataQualityError,
    build_freshness_report,
    data_fingerprint,
    enforce_quality_gate,
    run_data_quality_checks,
)
from observability.reporting import generate_corruption_report

RAW_RECORDS_PATH = Path(__file__).resolve().parents[1] / "data" / "raw" / "crossref_records.json"
FIXED_RUN_DATE = datetime(2026, 9, 25, tzinfo=UTC)
PHRASES = {"authors": "who authored", "date": "when was", "categories": "what categories"}


@pytest.fixture
def settings(tmp_path):
    return load_settings(tmp_path)


@pytest.fixture
def clean_df():
    return rebuild_clean_dataframe_from_raw(RAW_RECORDS_PATH, FIXED_RUN_DATE)


def test_clean_df_matches_contract(clean_df):
    assert set(REQUIRED_COLUMNS) <= set(clean_df.columns)


def test_quality_gate_passes_on_clean(clean_df, settings):
    report = run_data_quality_checks(clean_df, settings, "baseline")
    assert report["success"], report["failed_expectations"]
    assert (settings.paths.quality_dir / "baseline_quality_report.json").exists()


def test_quality_gate_reports_missing_column_without_crash(clean_df, settings):
    report = run_data_quality_checks(clean_df.drop(columns=["age_days"]), settings, "schema")
    assert not report["success"]
    assert any("age_days" in f for f in report["failed_expectations"])


def test_freshness(clean_df, settings):
    fresh = build_freshness_report(clean_df, settings, settings.paths.freshness_report)
    assert fresh["total_rows"] == len(clean_df)
    assert fresh["stale_rows"] == int((clean_df["age_days"] > settings.freshness_threshold_days).sum())
    stale = clean_df.assign(age_days=clean_df["age_days"] + 365)
    assert not build_freshness_report(stale, settings, settings.paths.freshness_report)["is_fresh"]


def test_freshness_is_warning_not_gate_failure(clean_df, settings):
    report = run_data_quality_checks(clean_df.assign(age_days=clean_df["age_days"] + 365), settings, "stale")
    assert report["success"] and not report["gx_suite_success"]
    assert any("(age_days)" in w for w in report["warnings"])


def test_enforce_gate_blocks_bad_data(clean_df, settings):
    enforce_quality_gate(run_data_quality_checks(clean_df, settings, "ok"))
    with pytest.raises(DataQualityError):
        enforce_quality_gate(run_data_quality_checks(clean_df.assign(title="short"), settings, "bad"))


def test_fingerprint_ignores_row_order_and_age(clean_df):
    shuffled = clean_df.iloc[::-1].assign(age_days=clean_df["age_days"][::-1] + 1)
    assert data_fingerprint(clean_df) == data_fingerprint(shuffled)
    assert data_fingerprint(clean_df) != data_fingerprint(clean_df.assign(title="x" * 20))


def test_repair_is_idempotent_by_fingerprint():
    first = rebuild_clean_dataframe_from_raw(RAW_RECORDS_PATH, FIXED_RUN_DATE)
    second = rebuild_clean_dataframe_from_raw(RAW_RECORDS_PATH, datetime(2026, 10, 1, tzinfo=UTC))
    assert data_fingerprint(first) == data_fingerprint(second)


def test_corruption_is_deterministic_and_logged(clean_df, settings):
    original = clean_df.copy(deep=True)
    a = corrupt_clean_dataframe(clean_df, settings.paths.corruption_log)
    b = corrupt_clean_dataframe(clean_df.sample(frac=1, random_state=7), settings.paths.corruption_log)
    pd.testing.assert_frame_equal(a, b)
    log = read_json(settings.paths.corruption_log)
    assert [c["name"] for c in log["scenarios"]] == [
        "drop_latest_records", "blank_summary", "inject_noise", "truncate_title", "stale_date", "duplicate_rows"
    ]
    pd.testing.assert_frame_equal(clean_df, original)  # input khong bi sua


def test_targeted_injection_hits_benchmark(clean_df, settings):
    ts = build_test_set(clean_df, settings.paths.eval_testset)
    corrupt_clean_dataframe(clean_df, settings.paths.corruption_log, test_set=ts)
    hits = {c["name"]: len(c["benchmark_hits"]) for c in read_json(settings.paths.corruption_log)["scenarios"]}
    for kind in ("drop_latest_records", "blank_summary", "inject_noise", "truncate_title", "stale_date"):
        assert hits[kind] >= 1, kind


def test_noise_breaks_tokens_but_keeps_length():
    text = "Retrieval augmented generation improves grounding."
    assert len(inject_noise(text)) > len(text)
    assert _token_f1(text, inject_noise(text)) < 0.3


def test_gate_catches_corruption(clean_df, settings):
    corrupted = corrupt_clean_dataframe(clean_df, settings.paths.corruption_log)
    report = run_data_quality_checks(corrupted, settings, "corrupted", reference_unique_ids=clean_df["paper_id"].nunique())
    failed = " ".join(report["failed_expectations"])
    for needle in ("values_to_be_unique(paper_id)", "unique_value_count", "(summary)", "(title)"):
        assert needle in failed, needle
    assert not build_freshness_report(corrupted, settings, settings.paths.freshness_report)["is_fresh"]


def test_testset_is_deterministic_answerable_and_matches_qa_wording(clean_df, settings):
    ts = build_test_set(clean_df, settings.paths.eval_testset)
    assert len(ts) == 10 and len({t["ground_truth_doc_ids"][0] for t in ts}) == 10
    assert all(t["ground_truth"].strip() for t in ts)
    assert build_test_set(clean_df.sample(frac=1, random_state=3), settings.paths.eval_testset) == ts
    for t in ts:
        if t["question_type"] in PHRASES:
            assert PHRASES[t["question_type"]] in t["question"].lower()
        assert t["question"].count("'") == 2


def test_testset_skips_unanswerable_type(clean_df, settings):
    # Crossref live co the khong tra `subject` -> categories rong: khong sinh cau hoi co ground truth rong.
    ts = build_test_set(clean_df.assign(categories_joined=""), settings.paths.eval_testset)
    assert len(ts) == 10 and all(t["ground_truth"].strip() for t in ts)
    assert "categories" not in {t["question_type"] for t in ts}


def test_report_and_dashboard_handle_missing_artifacts(settings):
    generate_corruption_report(settings.paths.comparison_report, {}, {}, {}, {}, {}, {}, {})
    assert "Chưa đủ artifacts" in build_dashboard(settings).read_text(encoding="utf-8")
