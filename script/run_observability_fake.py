"""Chay toan bo luong Observability tren DU LIEU GIA (khong can pipeline RAG, khong can model).

Output nam trong `.fake/data/...` (gitignored), khong dung vao `data/` that.
    python script/run_observability_fake.py
"""
from __future__ import annotations

from pathlib import Path

from core.config import load_settings
from evaluation.testset import build_test_set
from observability.dashboard import build_dashboard
from observability.fake_data import fake_evaluate, make_fake_clean_df
from observability.reporting import generate_phase1_report
from pipelines import corruption_flow

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    settings = load_settings(ROOT / ".fake")
    p = settings.paths

    # Phase 1 gia lap: clean df gia -> test set -> gate -> metrics proxy -> report
    df = make_fake_clean_df()
    corruption_flow.save_dataframe(df, p.clean_csv, p.clean_json)
    build_test_set(df, p.eval_testset)
    quality, freshness = corruption_flow.observe(df, settings, "baseline")
    metrics = fake_evaluate(settings, df, p.embeddings_json, p.baseline_metrics, p.baseline_answers)
    generate_phase1_report(p.baseline_report, {"source": "FAKE DATA", "records": len(df)}, metrics, quality, freshness)
    print(f"[baseline] gate={quality['success']} fresh={freshness['is_fresh']} metrics={metrics}")

    # Phase 2: dung corruption_flow that, chi thay evaluate + repair bang ban gia
    corruption_flow.main(settings, evaluate_fn=fake_evaluate, repair_fn=lambda s: make_fake_clean_df())
    print(f"Fake dashboard: {build_dashboard(settings, fake=True)}")


if __name__ == "__main__":
    main()
