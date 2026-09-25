from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from html import unescape
import re
from pathlib import Path
import time
from typing import Any

import requests

from core.config import Settings
from core.utils import normalize_whitespace, read_json, write_json, write_text


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_FETCH_ATTEMPTS = 4
_REQUEST_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class PaperRecord:
    paper_id: str
    title: str
    summary: str
    authors: list[str]
    categories: list[str]
    primary_category: str
    published: str
    updated: str
    abs_url: str
    pdf_url: str
    comment: str


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"<\s*br\s*/?\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:jats:)?(?:p|div|li|h[1-6])\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_whitespace(unescape(text))


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return _clean_text(value)


def _crossref_date(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    date_parts = value.get("date-parts")
    if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list):
        parts = date_parts[0]
        if parts:
            try:
                year = int(parts[0])
                month = int(parts[1]) if len(parts) > 1 else 1
                day = int(parts[2]) if len(parts) > 2 else 1
                return date(year, month, day).isoformat()
            except (TypeError, ValueError, OverflowError):
                return ""
    date_time = value.get("date-time")
    if isinstance(date_time, str):
        try:
            return date.fromisoformat(date_time[:10]).isoformat()
        except ValueError:
            return ""
    return ""


def _paper_date(item: dict[str, Any], *field_names: str) -> str:
    for field_name in field_names:
        parsed = _crossref_date(item.get(field_name))
        if parsed:
            return parsed
    return ""


def _parse_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for author in value:
        if isinstance(author, str):
            name = _clean_text(author)
        elif isinstance(author, dict):
            name = _clean_text(author.get("name"))
            if not name:
                parts = [
                    author.get("prefix"),
                    author.get("given"),
                    author.get("family"),
                    author.get("suffix"),
                ]
                name = _clean_text(" ".join(str(part) for part in parts if part))
        else:
            name = ""
        if name:
            authors.append(name)
    return authors


def _parse_categories(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    categories = [_clean_text(category) for category in value]
    return [category for category in categories if category]


def _pdf_url(item: dict[str, Any]) -> str:
    links = item.get("link")
    if not isinstance(links, list):
        return ""
    for link in links:
        if not isinstance(link, dict):
            continue
        url = link.get("URL") or link.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        content_type = str(link.get("content-type", "")).lower()
        if "pdf" in content_type or re.search(r"\.pdf(?:$|[?#])", url, re.IGNORECASE):
            return url.strip()
    return ""


def _record_from_item(item: dict[str, Any]) -> PaperRecord | None:
    paper_id = _clean_text(item.get("DOI") or item.get("doi"))
    title = _first_text(item.get("title"))
    summary = _clean_text(item.get("abstract"))
    if not paper_id or not title or not summary:
        return None

    categories = _parse_categories(item.get("subject"))
    published = _paper_date(
        item,
        "published",
        "published-print",
        "published-online",
        "issued",
        "created",
    )
    updated = _paper_date(item, "updated", "created", "indexed")
    return PaperRecord(
        paper_id=paper_id,
        title=title,
        summary=summary,
        authors=_parse_authors(item.get("author")),
        categories=categories,
        primary_category=categories[0] if categories else "",
        published=published,
        updated=updated,
        abs_url=_clean_text(item.get("URL")) or f"https://doi.org/{paper_id}",
        pdf_url=_pdf_url(item),
        comment=_clean_text(item.get("comment")),
    )


def parse_crossref_payload(payload: dict) -> list[PaperRecord]:
    """Parse Crossref work items into normalized, unique paper records."""
    if not isinstance(payload, dict):
        raise ValueError("Crossref payload must be a JSON object.")
    message = payload.get("message")
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        raise ValueError("Crossref payload is missing message.items as a list.")

    records: list[PaperRecord] = []
    seen_dois: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        record = _record_from_item(item)
        if record is None:
            continue
        identity = record.paper_id.casefold()
        if identity in seen_dois:
            continue
        seen_dois.add(identity)
        records.append(record)
    return records


def _fetch_live_payload(settings: Settings) -> tuple[dict[str, Any], str]:
    params = {
        "query": settings.source_query,
        "filter": settings.source_filter,
        "rows": settings.max_results,
        "select": (
            "DOI,title,abstract,author,subject,published,published-print,published-online,"
            "issued,created,indexed,URL,link"
        ),
    }
    last_error: Exception | None = None
    for attempt in range(_MAX_FETCH_ATTEMPTS):
        try:
            response = requests.get(
                "https://api.crossref.org/works",
                params=params,
                headers={"User-Agent": "ai-in-action-day10-data-pipeline/1.0"},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code in _RETRYABLE_STATUS_CODES:
                last_error = requests.HTTPError(
                    f"Crossref returned retryable HTTP {response.status_code}.",
                    response=response,
                )
                if attempt + 1 < _MAX_FETCH_ATTEMPTS:
                    time.sleep(2**attempt)
                    continue
                raise last_error
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
                raise ValueError("Crossref returned a malformed JSON response.")
            return payload, response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 >= _MAX_FETCH_ATTEMPTS:
                raise
            # Only retry network errors or the explicitly retryable statuses.
            if (
                isinstance(exc, requests.HTTPError)
                and getattr(exc.response, "status_code", None) not in _RETRYABLE_STATUS_CODES
            ):
                raise
            time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def _read_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = read_json(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Crossref snapshot not found: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
        raise ValueError(f"Crossref snapshot is malformed: {path}")
    return payload


def fetch_source_records(settings: Settings) -> list[PaperRecord]:
    """Load the checked-in snapshot by default, optionally refresh from Crossref."""
    response_path = settings.paths.raw_api_response
    payload: dict[str, Any]
    if settings.refresh_source:
        try:
            payload, raw_response = _fetch_live_payload(settings)
        except (requests.RequestException, ValueError) as exc:
            try:
                payload = _read_snapshot(response_path)
            except (FileNotFoundError, ValueError) as snapshot_error:
                raise RuntimeError(
                    f"Crossref refresh failed ({exc}) and no valid local snapshot is available ({snapshot_error})."
                ) from exc
        else:
            write_text(response_path, raw_response)
    else:
        payload = _read_snapshot(response_path)

    records = parse_crossref_payload(payload)
    if not records:
        raise ValueError(f"No valid Crossref records were parsed from {response_path}.")
    write_json(settings.paths.raw_records_json, [asdict(record) for record in records])
    return records


def load_raw_records(path: Path) -> list[PaperRecord]:
    """Load and validate serialized PaperRecord objects with record-level errors."""
    try:
        payload = read_json(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Raw Crossref records not found: {path}") from exc
    if not isinstance(payload, list):
        raise ValueError(f"Raw records file must contain a JSON array: {path}")

    string_fields = (
        "paper_id",
        "title",
        "summary",
        "primary_category",
        "published",
        "updated",
        "abs_url",
        "pdf_url",
        "comment",
    )
    records: list[PaperRecord] = []
    for index, item in enumerate(payload):
        location = f"{path} record[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{location} must be a JSON object.")
        missing = [field for field in (*string_fields, "authors", "categories") if field not in item]
        if missing:
            raise ValueError(f"{location} is missing required fields: {', '.join(missing)}.")
        invalid_strings = [field for field in string_fields if not isinstance(item[field], str)]
        if invalid_strings:
            raise ValueError(f"{location} fields must be strings: {', '.join(invalid_strings)}.")
        invalid_lists = [
            field
            for field in ("authors", "categories")
            if not isinstance(item[field], list) or any(not isinstance(value, str) for value in item[field])
        ]
        if invalid_lists:
            raise ValueError(f"{location} fields must be arrays of strings: {', '.join(invalid_lists)}.")
        if not item["paper_id"].strip() or not item["title"].strip() or not item["summary"].strip():
            raise ValueError(f"{location} must have non-empty paper_id, title, and summary.")
        records.append(PaperRecord(**{field: item[field] for field in PaperRecord.__dataclass_fields__}))
    return records
