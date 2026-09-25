from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any

import pandas as pd

from core.utils import write_json
from ingestion.cleaning import build_embedding_text


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _infer_run_date(df: pd.DataFrame) -> date:
    candidates: list[date] = []
    for _, row in df.iterrows():
        try:
            published = _to_date(row["published"])
            age_days = int(row["age_days"])
        except (TypeError, ValueError, OverflowError):
            continue
        if age_days > 0:
            candidates.append(published + timedelta(days=age_days))
    if candidates:
        counts = Counter(candidates)
        return counts.most_common(1)[0][0]
    if not df.empty:
        return max(_to_date(value) for value in df["published"])
    return datetime.now(timezone.utc).date()


def _refresh_derived_columns(df: pd.DataFrame, run_date: date) -> None:
    for index, row in df.iterrows():
        authors = row["authors"] if isinstance(row["authors"], list) else []
        categories = row["categories"] if isinstance(row["categories"], list) else []
        authors_joined = ", ".join(str(author) for author in authors if author)
        categories_joined = ", ".join(str(category) for category in categories if category)
        summary = "" if pd.isna(row["summary"]) else str(row["summary"])
        title = "" if pd.isna(row["title"]) else str(row["title"])
        published = _to_date(row["published"]).isoformat()
        df.at[index, "authors_joined"] = authors_joined
        df.at[index, "categories_joined"] = categories_joined
        df.at[index, "published"] = published
        df.at[index, "age_days"] = max(0, (run_date - date.fromisoformat(published)).days)
        df.at[index, "summary_chars"] = len(summary)
        df.at[index, "text_for_embedding"] = build_embedding_text(
            title,
            authors_joined,
            published,
            categories_joined,
            summary,
        )


def _selected_indices(length: int, count: int) -> list[int]:
    if length <= 0 or count <= 0:
        return []
    count = min(length, count)
    if count == 1:
        return [0]
    return sorted({round(position * (length - 1) / (count - 1)) for position in range(count)})


def _scenario(
    name: str,
    affected_ids: list[str],
    parameters: dict[str, Any],
    before: int,
    after: int,
) -> dict[str, Any]:
    return {
        "name": name,
        "affected_count": len(affected_ids),
        "affected_paper_ids": affected_ids,
        "parameters": parameters,
        "row_count_before": before,
        "row_count_after": after,
    }


def corrupt_clean_dataframe(df: pd.DataFrame, output_log_path: Path | str) -> pd.DataFrame:
    """Apply six deterministic data faults and write an auditable corruption log."""
    required = {
        "paper_id",
        "title",
        "summary",
        "authors",
        "categories",
        "published",
        "age_days",
        "authors_joined",
        "categories_joined",
        "summary_chars",
        "text_for_embedding",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Clean dataframe is missing required columns: {', '.join(missing)}.")

    reference_date = _infer_run_date(df)
    corrupted = df.copy(deep=True)
    corrupted = corrupted.sort_values(
        by=["published", "paper_id"],
        ascending=[False, True],
        kind="mergesort",
        ignore_index=True,
    )
    original_count = len(corrupted)
    scenarios: list[dict[str, Any]] = []

    drop_count = min(max(0, original_count - 1), math.ceil(original_count * 0.2))
    before = len(corrupted)
    dropped = corrupted.iloc[:drop_count]["paper_id"].astype(str).tolist()
    corrupted = corrupted.iloc[drop_count:].reset_index(drop=True)
    scenarios.append(
        _scenario(
            "drop_latest_records",
            dropped,
            {"fraction": 0.2, "dropped_count": drop_count},
            before,
            len(corrupted),
        )
    )

    if len(corrupted):
        # Each single-row corruption uses a fixed rank in the stable sorted set.
        blank_index = 0
        before = len(corrupted)
        blank_id = str(corrupted.at[blank_index, "paper_id"])
        corrupted.at[blank_index, "summary"] = ""
        _refresh_derived_columns(corrupted, reference_date)
        scenarios.append(
            _scenario("blank_summary", [blank_id], {"selected_rank": blank_index}, before, len(corrupted))
        )

        noise_index = min(1, len(corrupted) - 1)
        before = len(corrupted)
        noise_id = str(corrupted.at[noise_index, "paper_id"])
        summary = str(corrupted.at[noise_index, "summary"] or "")
        corrupted.at[noise_index, "summary"] = f"{summary} [NOISE_###@@@%%%]".strip()
        _refresh_derived_columns(corrupted, reference_date)
        scenarios.append(
            _scenario(
                "inject_noise",
                [noise_id],
                {"marker": "[NOISE_###@@@%%%]", "selected_rank": noise_index},
                before,
                len(corrupted),
            )
        )

        title_index = min(2, len(corrupted) - 1)
        before = len(corrupted)
        title_id = str(corrupted.at[title_index, "paper_id"])
        corrupted.at[title_index, "title"] = str(corrupted.at[title_index, "title"])[:7]
        _refresh_derived_columns(corrupted, reference_date)
        scenarios.append(
            _scenario(
                "truncate_title",
                [title_id],
                {"max_characters": 7, "selected_rank": title_index},
                before,
                len(corrupted),
            )
        )

        stale_count = math.ceil(len(corrupted) * 0.3)
        stale_indices = _selected_indices(len(corrupted), stale_count)
        before = len(corrupted)
        stale_ids = corrupted.loc[stale_indices, "paper_id"].astype(str).tolist()
        for index in stale_indices:
            old_date = _to_date(corrupted.at[index, "published"])
            corrupted.at[index, "published"] = (old_date - timedelta(days=365)).isoformat()
        _refresh_derived_columns(corrupted, reference_date)
        scenarios.append(
            _scenario(
                "stale_date",
                stale_ids,
                {"days_subtracted": 365, "fraction": 0.3, "selected_ranks": stale_indices},
                before,
                len(corrupted),
            )
        )

        duplicate_count = min(len(corrupted), max(1, math.ceil(len(corrupted) * 0.1)))
        duplicate_indices = list(range(duplicate_count))
        before = len(corrupted)
        duplicate_ids = corrupted.loc[duplicate_indices, "paper_id"].astype(str).tolist()
        duplicates = corrupted.iloc[duplicate_indices].copy(deep=True)
        corrupted = pd.concat([corrupted, duplicates], ignore_index=True)
        _refresh_derived_columns(corrupted, reference_date)
        scenarios.append(
            _scenario(
                "duplicate_rows",
                duplicate_ids,
                {"duplicate_count": duplicate_count, "selected_ranks": duplicate_indices},
                before,
                len(corrupted),
            )
        )
    else:
        for name, parameters in (
            ("blank_summary", {"selected_rank": None}),
            ("inject_noise", {"marker": "[NOISE_###@@@%%%]", "selected_rank": None}),
            ("truncate_title", {"max_characters": 7, "selected_rank": None}),
            ("stale_date", {"days_subtracted": 365, "fraction": 0.3, "selected_ranks": []}),
            ("duplicate_rows", {"duplicate_count": 0, "selected_ranks": []}),
        ):
            scenarios.append(_scenario(name, [], parameters, len(corrupted), len(corrupted)))

    log = {
        "version": 1,
        "input_rows": original_count,
        "output_rows": len(corrupted),
        "run_date": reference_date.isoformat(),
        "scenarios": scenarios,
    }
    write_json(Path(output_log_path), log)
    return corrupted
