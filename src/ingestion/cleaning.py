from __future__ import annotations

from datetime import date, datetime, timezone
from html import unescape
import re
from pathlib import Path
from typing import Any

import pandas as pd

from core.utils import normalize_whitespace
from ingestion.crossref import PaperRecord, load_raw_records


CLEAN_COLUMNS = [
    "paper_id",
    "title",
    "summary",
    "authors",
    "categories",
    "primary_category",
    "published",
    "updated",
    "age_days",
    "authors_joined",
    "categories_joined",
    "summary_chars",
    "text_for_embedding",
    "abs_url",
    "pdf_url",
    "comment",
]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"<\s*br\s*/?\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:jats:)?(?:p|div|li|h[1-6])\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_whitespace(unescape(text))


def _clean_list(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    return [cleaned for value in values if (cleaned := _clean_text(value))]


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def build_embedding_text(
    title: str,
    authors_joined: str,
    published: str,
    categories_joined: str,
    summary: str,
) -> str:
    """Return the stable five-part text representation used by retrieval."""
    return "\n".join(
        (
            f"Title: {title}",
            f"Authors: {authors_joined}",
            f"Published: {published}",
            f"Categories: {categories_joined}",
            f"Summary: {summary}",
        )
    )


def _run_date_value(run_date: datetime | date) -> date:
    if isinstance(run_date, datetime):
        if run_date.tzinfo is not None:
            return run_date.astimezone(timezone.utc).date()
        return run_date.date()
    return run_date


def build_clean_dataframe(records: list[PaperRecord], run_date: datetime) -> pd.DataFrame:
    """Normalize Crossref records into the deterministic clean-paper contract."""
    reference_date = _run_date_value(run_date)
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for record in records:
        paper_id = _clean_text(record.paper_id)
        title = _clean_text(record.title)
        summary = _clean_text(record.summary)
        published_date = _parse_date(record.published)
        if not paper_id or not title or not summary or published_date is None:
            continue
        identity = paper_id.casefold()
        if identity in seen_ids:
            continue
        seen_ids.add(identity)

        authors = _clean_list(record.authors)
        categories = _clean_list(record.categories)
        primary_category = _clean_text(record.primary_category) or (categories[0] if categories else "")
        authors_joined = ", ".join(authors)
        categories_joined = ", ".join(categories)
        published = published_date.isoformat()
        updated_date = _parse_date(record.updated)
        updated = updated_date.isoformat() if updated_date else ""
        age_days = max(0, (reference_date - published_date).days)

        rows.append(
            {
                "paper_id": paper_id,
                "title": title,
                "summary": summary,
                "authors": authors,
                "categories": categories,
                "primary_category": primary_category,
                "published": published,
                "updated": updated,
                "age_days": age_days,
                "authors_joined": authors_joined,
                "categories_joined": categories_joined,
                "summary_chars": len(summary),
                "text_for_embedding": build_embedding_text(
                    title,
                    authors_joined,
                    published,
                    categories_joined,
                    summary,
                ),
                "abs_url": _clean_text(record.abs_url),
                "pdf_url": _clean_text(record.pdf_url),
                "comment": _clean_text(record.comment),
            }
        )

    frame = pd.DataFrame(rows, columns=CLEAN_COLUMNS)
    if not frame.empty:
        frame = frame.sort_values(
            by=["published", "paper_id"],
            ascending=[False, True],
            kind="mergesort",
            ignore_index=True,
        )
    return frame


def rebuild_clean_dataframe_from_raw(raw_records_path: Path, run_date: datetime) -> pd.DataFrame:
    """Recreate clean data from the preserved raw records for idempotent repair."""
    return build_clean_dataframe(load_raw_records(raw_records_path), run_date)
