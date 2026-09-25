"""Test Observability tren du lieu gia. Chay: python -m pytest tests -q"""
from __future__ import annotations

import pandas as pd
import pytest

from core.config import load_settings
from core.utils import read_json
from evaluation.testset import build_test_set
from ingestion.corruption import corrupt_clean_dataframe
from observability.dashboard import build_dashboard
from observability.fake_data import fake_evaluate, make_fake_clean_df
from observability.quality import REQUIRED_COLUMNS, build_freshness_report, run_data_quality_checks
from observability.reporting import generate_corruption_report
from pipelines import corruption_flow


@pytest.fixture
def settings(tmp_path):
    return load_settings(tmp_path)


@pytest.fixture
def clean_df():
    return make_fake_clean_df()


def test_fake_df_matches_contract(clean_df):
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
    assert fresh["is_fresh"] and fresh["stale_rows"] == 1
    stale = clean_df.assign(age_days=clean_df["age_days"] + 365)
    assert not build_freshness_report(stale, settings, settings.paths.freshness_report)["is_fresh"]


def test_corruption_is_deterministic_and_logged(clean_df, settings):
    a = corrupt_clean_dataframe(clean_df, settings.paths.corruption_log)
    b = corrupt_clean_dataframe(clean_df, settings.paths.corruption_log)
    pd.testing.assert_frame_equal(a, b)
    log = read_json(settings.paths.corruption_log)
    assert [c["name"] for c in log["scenarios"]] == [
        "drop_latest_records", "blank_summary", "inject_noise", "truncate_title", "stale_date", "duplicate_rows"
    ]
    assert clean_df.equals(make_fake_clean_df()), "input df khong duoc bi sua"


def test_targeted_injection_hits_benchmark(clean_df, settings):
    ts = build_test_set(clean_df, settings.paths.eval_testset)
    corrupt_clean_dataframe(clean_df, settings.paths.corruption_log, test_set=ts)
    log = read_json(settings.paths.corruption_log)
    hits = {c["name"]: len(c["benchmark_hits"]) for c in log["scenarios"]}
    for kind in ("drop_latest_records", "blank_summary", "inject_noise", "truncate_title", "stale_date"):
        assert hits[kind] >= 1, kind


def test_noise_breaks_tokens():
    from ingestion.corruption import inject_noise
    from observability.fake_data import _token_f1

    text = "Retrieval augmented generation improves grounding."
    assert len(inject_noise(text)) > len(text)
    assert _token_f1(text, inject_noise(text)) < 0.3


def test_gate_catches_corruption(clean_df, settings):
    corrupted = corrupt_clean_dataframe(clean_df, settings.paths.corruption_log)
    report = run_data_quality_checks(corrupted, settings, "corrupted", reference_unique_ids=len(clean_df))
    failed = " ".join(report["failed_expectations"])
    for needle in ("values_to_be_unique(paper_id)", "unique_value_count", "(summary)", "(title)"):
        assert needle in failed, needle
    # Freshness la warning (theo Guide), khong phai critical
    assert any("(age_days)" in w for w in report["warnings"])
    assert not build_freshness_report(corrupted, settings, settings.paths.freshness_report)["is_fresh"]


def test_freshness_is_warning_not_gate_failure(clean_df, settings):
    stale = clean_df.assign(age_days=clean_df["age_days"] + 365)
    report = run_data_quality_checks(stale, settings, "stale")
    assert report["success"] and report["warnings"] and not report["gx_suite_success"]


def test_enforce_gate_blocks_bad_data(clean_df, settings):
    from observability.quality import DataQualityError, enforce_quality_gate

    enforce_quality_gate(run_data_quality_checks(clean_df, settings, "ok"))
    with pytest.raises(DataQualityError):
        enforce_quality_gate(run_data_quality_checks(clean_df.assign(title="short"), settings, "bad"))


def test_fingerprint_ignores_row_order_and_age(clean_df):
    from observability.quality import data_fingerprint

    shuffled = clean_df.iloc[::-1].assign(age_days=clean_df["age_days"][::-1] + 1)
    assert data_fingerprint(clean_df) == data_fingerprint(shuffled)
    assert data_fingerprint(clean_df) != data_fingerprint(clean_df.assign(title="x" * 20))


def test_testset_shape_and_qa_wording(clean_df, settings):
    ts = build_test_set(clean_df, settings.paths.eval_testset)
    assert len(ts) == 10 and len({t["ground_truth_doc_ids"][0] for t in ts}) == 10
    counts = pd.Series([t["question_type"] for t in ts]).value_counts().to_dict()
    assert counts == {"summary": 3, "authors": 3, "date": 2, "categories": 2}
    assert build_test_set(clean_df, settings.paths.eval_testset) == ts
    # Contract voi retrieval/qa.py: cum tu dinh tuyen cau tra loi
    phrases = {"authors": "who authored", "date": "when was", "categories": "what categories"}
    for t in ts:
        if t["question_type"] in phrases:
            assert phrases[t["question_type"]] in t["question"].lower()
        assert t["question"].count("'") == 2


def test_testset_skips_unanswerable_type(clean_df, settings):
    # Crossref live co the khong tra `subject` -> categories rong: khong duoc sinh cau hoi co ground truth rong.
    ts = build_test_set(clean_df.assign(categories_joined=""), settings.paths.eval_testset)
    assert len(ts) == 10
    assert all(t["ground_truth"].strip() for t in ts)
    assert "categories" not in {t["question_type"] for t in ts}


def test_full_flow_on_fake_data(clean_df, settings):
    p = settings.paths
    corruption_flow.save_dataframe(clean_df, p.clean_csv, p.clean_json)
    build_test_set(clean_df, p.eval_testset)
    fake_evaluate(settings, clean_df, None, p.baseline_metrics, p.baseline_answers)

    result = corruption_flow.main(settings, evaluate_fn=fake_evaluate, repair_fn=lambda s: make_fake_clean_df())

    assert result["corrupted"]["mean_token_f1"] < result["baseline"]["mean_token_f1"]
    assert result["repaired"] == result["baseline"]
    assert result["auto_repair"]["triggered"] and result["auto_repair"]["repaired_gate_pass"]
    assert result["auto_repair"]["fingerprint_match"]
    report_q = read_json(p.quality_dir / "corrupted_quality_report.json")
    assert "expect_column_unique_value_count_to_be_between(paper_id)" in report_q["failed_expectations"]
    report = p.comparison_report.read_text(encoding="utf-8")
    assert "| Baseline | Corrupted | Repaired |" in report
    assert (p.comparison_report.parent / "dashboard.html").exists()


def test_report_and_dashboard_handle_missing_artifacts(settings):
    generate_corruption_report(settings.paths.comparison_report, {}, {}, {}, {}, {}, {}, {})
    assert "Chưa đủ artifacts" in build_dashboard(settings).read_text(encoding="utf-8")
