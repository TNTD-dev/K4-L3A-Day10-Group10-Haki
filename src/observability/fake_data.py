"""Du lieu gia + evaluator proxy de phat trien Observability truoc khi merge voi pipeline RAG.

KHONG dung cho bai nop: metrics o day la mo phong (lookup chinh xac thay cho vector search).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean

import pandas as pd

from core.utils import first_sentence, read_json, write_json
from ingestion.cleaning import build_embedding_text

RUN_DATE = datetime(2026, 9, 25)
TOPICS = [
    ("Agentic Retrieval for Multi-hop Question Answering", "Artificial Intelligence; Information Retrieval"),
    ("Data Quality Gates for Production RAG Systems", "Software Engineering; Data Systems"),
    ("Freshness Monitoring of Vector Stores", "Data Systems; Databases"),
    ("Evaluating Faithfulness in Retrieval Augmented Generation", "Computation and Language"),
    ("Hybrid Sparse Dense Retrieval at Scale", "Information Retrieval"),
    ("Schema Drift Detection in Streaming Pipelines", "Data Systems"),
]


def make_fake_clean_df(n: int = 24, run_date: datetime = RUN_DATE) -> pd.DataFrame:
    """Dataframe gia dung schema contract cua cleaning (xem quality.REQUIRED_COLUMNS)."""
    rows = []
    for i in range(n):
        topic, categories = TOPICS[i % len(TOPICS)]
        age = 20 + i * 7  # 20..181 ngay -> 1 bai stale, giong snapshot that
        published = (run_date - timedelta(days=age)).strftime("%Y-%m-%d")
        summary = (
            f"This study number {i} investigates {topic.lower()} with a benchmark of {100 + i} tasks. "
            f"Results show a {5 + i % 7} point gain over strong baselines."
        )
        row = {
            "paper_id": f"10.5555/fake.{i:04d}",
            "title": f"{topic} Part {i + 1}",
            "summary": summary,
            "authors_joined": f"Author A{i}, Author B{i}",
            "categories_joined": categories,
            "primary_category": categories.split(";")[0],
            "published": published,
            "age_days": age,
            "summary_chars": len(summary),
            "abs_url": f"https://doi.org/10.5555/fake.{i:04d}",
            "pdf_url": f"https://doi.org/10.5555/fake.{i:04d}",
        }
        row["text_for_embedding"] = build_embedding_text(row["title"], row["authors_joined"], row["published"], row["categories_joined"], row["summary"])
        rows.append(row)
    return pd.DataFrame(rows)


def _token_f1(reference: str, prediction: str) -> float:
    # Ban sao gon cua evaluation.metrics._token_f1 (tranh import torch/chroma khi chay fake).
    ref, pred = set(reference.lower().split()), set(prediction.lower().split())
    overlap = len(ref & pred)
    if not overlap:
        return 0.0
    p, r = overlap / len(pred), overlap / len(ref)
    return 2 * p * r / (p + r)


def fake_evaluate(settings, df: pd.DataFrame, embeddings_path, metrics_path, answers_path) -> dict:
    """Proxy cua evaluate_pipeline: tra loi bang lookup title chinh xac (giong qa.py khi exact match)."""
    by_title = {str(t).lower(): r for t, r in zip(df["title"], df.to_dict("records"))}
    field = {"authors": "authors_joined", "date": "published", "categories": "categories_joined"}
    answers = []
    for item in read_json(settings.paths.eval_testset):
        title = item["question"].split("'")[1].lower()
        row = by_title.get(title)
        if row is None:
            answer, hit = "", item["ground_truth_doc_ids"][0] in set(df["paper_id"])
        else:
            kind = item["question_type"]
            answer = first_sentence(row["summary"]) if kind == "summary" else str(row[field[kind]])
            hit = True
        f1 = _token_f1(item["ground_truth"], answer)
        score = 5 if f1 >= 0.95 else 3 if f1 >= 0.5 else 1
        answers.append({**item, "answer": answer, "retrieval_hit": hit, "token_f1": f1, "judge": {"score": score, "correct": score >= 3}})
    summary = {
        "samples": len(answers),
        "retrieval_hit_rate": mean(1.0 if a["retrieval_hit"] else 0.0 for a in answers),
        "mean_token_f1": mean(a["token_f1"] for a in answers),
        "judge_accuracy": mean(1.0 if a["judge"]["correct"] else 0.0 for a in answers),
        "mean_judge_score": mean(a["judge"]["score"] for a in answers),
        "simulated": True,
    }
    write_json(metrics_path, summary)
    write_json(answers_path, answers)
    return summary
