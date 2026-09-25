from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from core.config import load_settings
from ingestion.cleaning import CLEAN_COLUMNS, build_clean_dataframe, rebuild_clean_dataframe_from_raw
from ingestion.corruption import corrupt_clean_dataframe
from ingestion.crossref import PaperRecord, load_raw_records, parse_crossref_payload
from retrieval import embeddings as embedding_module
from retrieval.index import LocalEmbeddingIndex


REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "data" / "raw" / "crossref_response.json"
RAW_RECORDS_PATH = REPO_ROOT / "data" / "raw" / "crossref_records.json"
FIXED_RUN_DATE = datetime(2026, 9, 25, tzinfo=UTC)


def _sample_item(doi: str = "10.5555/paper.1") -> dict:
    return {
        "DOI": doi,
        "title": ["  Example &amp; Paper  "],
        "abstract": "<jats:p>A useful <jats:italic>abstract</jats:italic> with enough detail.</jats:p>",
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "subject": ["Artificial Intelligence"],
        "published": {"date-parts": [[2026, 2, 3]]},
        "updated": {"date-time": "2026-03-04T10:30:00Z"},
        "URL": f"https://doi.org/{doi}",
        "link": [
            {"URL": "https://example.org/article.xml", "content-type": "application/xml"},
            {"URL": "https://example.org/article.pdf", "content-type": "application/pdf"},
        ],
    }


def test_parse_crossref_payload_normalizes_and_deduplicates() -> None:
    duplicate = _sample_item()
    duplicate["title"] = ["A duplicate DOI"]
    payload = {
        "message": {
            "items": [
                _sample_item(),
                duplicate,
                {"DOI": "10.5555/missing-abstract", "title": ["No abstract"]},
                {"title": ["No DOI"], "abstract": "An abstract"},
            ]
        }
    }

    records = parse_crossref_payload(payload)

    assert len(records) == 1
    record = records[0]
    assert record.paper_id == "10.5555/paper.1"
    assert record.title == "Example & Paper"
    assert record.summary == "A useful abstract with enough detail."
    assert record.authors == ["Ada Lovelace"]
    assert record.categories == ["Artificial Intelligence"]
    assert record.published == "2026-02-03"
    assert record.updated == "2026-03-04"
    assert record.pdf_url == "https://example.org/article.pdf"


def test_load_raw_records_reports_record_location(tmp_path: Path) -> None:
    path = tmp_path / "bad-records.json"
    path.write_text(json.dumps([{"paper_id": "10.5555/bad"}]), encoding="utf-8")

    with pytest.raises(ValueError, match=r"record\[0\].*missing required fields"):
        load_raw_records(path)


def test_snapshot_cleaning_has_stable_schema_and_values() -> None:
    payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    records = parse_crossref_payload(payload)

    clean = build_clean_dataframe(records, FIXED_RUN_DATE)

    assert len(clean) == 24
    assert clean.columns.tolist() == CLEAN_COLUMNS
    assert clean["paper_id"].is_unique
    assert clean[["paper_id", "title", "text_for_embedding"]].notna().all().all()
    assert clean["age_days"].ge(0).all()
    assert (clean["summary_chars"] == clean["summary"].str.len()).all()
    assert clean.iloc[0]["text_for_embedding"].splitlines()[0] == f"Title: {clean.iloc[0]['title']}"
    pd.testing.assert_frame_equal(
        clean,
        build_clean_dataframe(records, FIXED_RUN_DATE),
    )


def test_corruption_is_deterministic_and_logs_all_scenarios(tmp_path: Path) -> None:
    records = load_raw_records(RAW_RECORDS_PATH)
    clean = build_clean_dataframe(records, FIXED_RUN_DATE)
    log_path = tmp_path / "corruption_log.json"

    corrupted = corrupt_clean_dataframe(clean, log_path)
    repeated = corrupt_clean_dataframe(clean.sample(frac=1, random_state=13), tmp_path / "again.json")
    log = json.loads(log_path.read_text(encoding="utf-8"))

    assert [scenario["name"] for scenario in log["scenarios"]] == [
        "drop_latest_records",
        "blank_summary",
        "inject_noise",
        "truncate_title",
        "stale_date",
        "duplicate_rows",
    ]
    assert log["input_rows"] == 24
    assert log["output_rows"] == len(corrupted)
    assert len(corrupted) == 22  # 24 - 5 drop + 3 duplicate
    assert corrupted["paper_id"].duplicated().any()
    assert (corrupted["summary"] == "").any()
    assert corrupted["summary"].str.contains("~#", regex=False).any()
    assert corrupted["title"].str.len().lt(8).any()
    assert corrupted["age_days"].gt(clean["age_days"].max()).any()
    assert corrupted["text_for_embedding"].str.contains("Summary:", regex=False).all()
    pd.testing.assert_frame_equal(
        corrupted.reset_index(drop=True),
        repeated.reset_index(drop=True),
    )


def test_repair_rebuilds_same_clean_data_from_raw_snapshot() -> None:
    first = rebuild_clean_dataframe_from_raw(RAW_RECORDS_PATH, FIXED_RUN_DATE)
    second = rebuild_clean_dataframe_from_raw(RAW_RECORDS_PATH, FIXED_RUN_DATE)

    pd.testing.assert_frame_equal(first, second)


class FakeEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def test_chroma_index_build_load_search_and_manifest_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_settings = load_settings(project_dir=REPO_ROOT)
    paths = replace(
        base_settings.paths,
        chroma_dir=tmp_path / "chroma",
        embeddings_json=tmp_path / "embeddings.json",
        corrupted_embeddings_json=tmp_path / "embeddings-corrupted.json",
        repaired_embeddings_json=tmp_path / "embeddings-repaired.json",
    )
    settings = replace(
        base_settings,
        paths=paths,
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
    )
    monkeypatch.setattr(embedding_module, "build_embeddings", lambda _: FakeEmbeddings())
    monkeypatch.setattr("retrieval.index.build_embeddings", lambda _: FakeEmbeddings())
    clean = build_clean_dataframe(load_raw_records(RAW_RECORDS_PATH), FIXED_RUN_DATE).head(3)

    index = LocalEmbeddingIndex.build(clean, settings)
    corrupted = corrupt_clean_dataframe(clean, tmp_path / "corruption-log.json")
    corrupted_index = LocalEmbeddingIndex.build(
        corrupted,
        settings,
        paths.corrupted_embeddings_json,
    )
    repaired_index = LocalEmbeddingIndex.build(
        clean,
        settings,
        paths.repaired_embeddings_json,
    )
    loaded = LocalEmbeddingIndex.load(settings)
    results = loaded.search("agent retrieval", top_k=2)

    assert index.collection_name == "papers-baseline"
    assert corrupted_index.collection_name == "papers-corrupted"
    assert repaired_index.collection_name == "papers-repaired"
    assert loaded.collection_name == "papers-baseline"
    assert len(results) == 2
    assert results[0].paper_id in clean["paper_id"].tolist()
    manifest = json.loads(paths.embeddings_json.read_text(encoding="utf-8"))
    assert manifest["embedding_provider"] == "openai"
    assert manifest["embedding_model"] == "text-embedding-3-small"
    assert manifest["collection_name"] == "papers-baseline"

    wrong_model_settings = replace(settings, embedding_model="text-embedding-3-large")
    with pytest.raises(RuntimeError, match="rebuild the index"):
        LocalEmbeddingIndex.load(wrong_model_settings)
