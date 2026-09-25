from __future__ import annotations

from core.config import load_settings
from core.utils import now_utc, write_json
from evaluation.metrics import evaluate_pipeline
from evaluation.testset import build_test_set
from ingestion.cleaning import build_clean_dataframe
from ingestion.crossref import fetch_source_records
from observability.quality import build_freshness_report, enforce_quality_gate, run_data_quality_checks
from observability.reporting import generate_phase1_report
from pipelines.corruption_flow import save_dataframe
from retrieval.index import LocalEmbeddingIndex

DEMO_QUESTION_COUNT = 2


def _agent_demo(settings, index, test_set) -> None:
    """Demo agent (tuy chon): loi LLM/network khong duoc lam hong baseline."""
    try:
        from retrieval.agent import build_agent, run_agent_question

        agent = build_agent(settings, index)
        answers = [
            {"question": item["question"], "answer": run_agent_question(agent, item["question"])}
            for item in test_set[:DEMO_QUESTION_COUNT]
        ]
        write_json(settings.paths.demo_answers, answers)
        print(f"[agent] demo -> {settings.paths.demo_answers}")
    except Exception as exc:
        print(f"[agent] demo skipped: {exc}")


def main() -> None:
    settings = load_settings()
    p = settings.paths
    run_date = now_utc()

    # 1-2. Ingest (live Crossref neu REFRESH_SOURCE, fallback snapshot) -> raw artifacts
    records = fetch_source_records(settings)

    # 3-4. Clean + luu
    df = build_clean_dataframe(records, run_date)
    save_dataframe(df, p.clean_csv, p.clean_json)
    print(f"[clean] {len(records)} records -> {len(df)} clean rows")

    # 5. Quality Gate + Freshness TRUOC khi index: data xau khong vao vector store
    quality = run_data_quality_checks(df, settings, "baseline")
    freshness = build_freshness_report(df, settings, p.freshness_report)
    print(f"[gate] baseline quality={quality['success']} warnings={quality['warnings']} fresh={freshness['is_fresh']}")
    enforce_quality_gate(quality)

    # 6. Index
    index = LocalEmbeddingIndex.build(df, settings, p.embeddings_json)
    print(f"[index] {index.collection_name}: {len(index.documents)} docs ({settings.embedding_provider}/{settings.embedding_model})")

    # 7. Test set: builder tat dinh -> cung data cho cung de; corruption_flow tai su dung file nay
    test_set = build_test_set(df, p.eval_testset)

    # 8. Evaluate
    metrics = evaluate_pipeline(settings, index, p.eval_testset, p.baseline_metrics, p.baseline_answers).summary
    print(f"[eval] hit_rate={metrics['retrieval_hit_rate']:.4f} token_f1={metrics['mean_token_f1']:.4f} "
          f"judge_acc={metrics['judge_accuracy']:.4f}")

    # 9. Report
    source_summary = {
        "source": settings.source_api,
        "query": settings.source_query,
        "live_refresh": settings.refresh_source,
        "raw_records": len(records),
        "clean_rows": len(df),
        "published_range": f"{df['published'].min()} → {df['published'].max()}",
        "run_date": run_date.date().isoformat(),
        "embedding": f"{settings.embedding_provider}/{settings.embedding_model}",
        "llm": f"{settings.llm_provider}/{settings.model_name}",
        "collection": index.collection_name,
        "test_questions": len(test_set),
    }
    generate_phase1_report(p.baseline_report, source_summary, metrics, quality, freshness)
    print(f"[report] {p.baseline_report}")

    # 10. Agent demo
    _agent_demo(settings, index, test_set)
