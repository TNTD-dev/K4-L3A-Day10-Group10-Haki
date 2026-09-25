from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import pandas as pd

from core.config import Settings
from core.utils import read_json


def _read_optional_json(path: Path) -> Any:
    try:
        return read_json(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _read_rows(path: Path) -> list[dict[str, Any]]:
    payload = _read_optional_json(path)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [row for row in payload["data"] if isinstance(row, dict)]
    return []


def _read_recent_events(path: Path, limit: int = 500) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as event_file:
            for line in event_file:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except OSError:
        return []
    return list(events)


def _collection_counts(settings: Settings) -> dict[str, int | None]:
    manifests = {
        settings.baseline_collection_name: settings.paths.embeddings_json,
        settings.corrupted_collection_name: settings.paths.corrupted_embeddings_json,
        settings.repaired_collection_name: settings.paths.repaired_embeddings_json,
    }
    counts: dict[str, int | None] = {name: None for name in manifests}
    for name, manifest_path in manifests.items():
        manifest = _read_optional_json(manifest_path)
        if isinstance(manifest, dict) and manifest.get("collection_name") == name:
            documents = manifest.get("documents")
            if isinstance(documents, list):
                counts[name] = len(documents)
    return counts


def _age_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    buckets = {"0–30 days": 0, "31–90 days": 0, "91–180 days": 0, ">180 days": 0}
    for row in rows:
        try:
            age = max(0, int(float(row.get("age_days", 0))))
        except (TypeError, ValueError, OverflowError):
            continue
        if age <= 30:
            buckets["0–30 days"] += 1
        elif age <= 90:
            buckets["31–90 days"] += 1
        elif age <= 180:
            buckets["91–180 days"] += 1
        else:
            buckets[">180 days"] += 1
    return buckets


def _freshness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"status": "waiting", "total_rows": 0, "stale_rows": None, "stale_ratio": None}
    ages = pd.to_numeric(pd.Series([row.get("age_days") for row in rows]), errors="coerce")
    stale_rows = int(ages.gt(180).sum())
    valid_rows = int(ages.notna().sum())
    ratio = stale_rows / len(rows)
    return {
        "status": "healthy" if valid_rows == len(rows) and ratio <= 0.25 else "alert",
        "total_rows": len(rows),
        "stale_rows": stale_rows,
        "stale_ratio": ratio,
        "threshold_days": 180,
        "max_stale_ratio": 0.25,
    }


def _read_metrics(settings: Settings) -> dict[str, Any]:
    return {
        "baseline": _read_optional_json(settings.paths.baseline_metrics),
        "corrupted": _read_optional_json(settings.paths.corrupted_metrics),
        "repaired": _read_optional_json(settings.paths.repaired_metrics),
    }


def _artifact_status(settings: Settings) -> list[dict[str, Any]]:
    candidates = [
        ("Crossref raw records", settings.paths.raw_records_json),
        ("Clean dataset", settings.paths.clean_json),
        ("Corrupted dataset", settings.paths.corrupted_clean_json),
        ("Repaired dataset", settings.paths.repaired_clean_json),
        ("Baseline embedding manifest", settings.paths.embeddings_json),
        ("Corrupted embedding manifest", settings.paths.corrupted_embeddings_json),
        ("Repaired embedding manifest", settings.paths.repaired_embeddings_json),
        ("Self-healing event history", settings.paths.self_healing_events_jsonl),
        ("Latest self-healing run", settings.paths.self_healing_latest_json),
        ("Baseline retrieval metrics", settings.paths.baseline_metrics),
        ("GX baseline report", settings.paths.baseline_quality_report),
        ("GX corrupted report", settings.paths.corrupted_quality_report),
        ("Freshness report", settings.paths.freshness_report),
        ("Corrupted retrieval metrics", settings.paths.corrupted_metrics),
        ("Repaired retrieval metrics", settings.paths.repaired_metrics),
    ]
    artifacts = []
    for name, path in candidates:
        try:
            display_path = str(path.relative_to(settings.paths.project_dir))
        except ValueError:
            display_path = str(path)
        artifacts.append({"name": name, "path": display_path, "exists": path.exists()})
    return artifacts


def build_snapshot(settings: Settings) -> dict[str, Any]:
    latest = _read_optional_json(settings.paths.self_healing_latest_json)
    latest = latest if isinstance(latest, dict) else None
    clean_rows = _read_rows(settings.paths.clean_json)
    repaired_rows = _read_rows(settings.paths.repaired_clean_json)
    corrupted_rows = _read_rows(settings.paths.corrupted_clean_json)
    events = _read_recent_events(settings.paths.self_healing_events_jsonl, limit=500)
    counts = _collection_counts(settings)

    quality_report = None
    if latest:
        quality_report = latest.get("post_repair_checks") or latest.get("pre_repair_checks")
    if quality_report is None:
        quality_report = _read_optional_json(settings.paths.baseline_quality_report)
    gx_artifacts = {
        "baseline": _read_optional_json(settings.paths.baseline_quality_report),
        "corrupted": _read_optional_json(settings.paths.corrupted_quality_report),
        "freshness": _read_optional_json(settings.paths.freshness_report),
    }

    latest_state = latest.get("state") if latest else "WAITING"
    if latest_state == "HEALTHY":
        pipeline_health = "healthy"
    elif latest_state == "REPAIR_FAILED":
        pipeline_health = "failed"
    elif latest_state in {"CORRUPTING", "DETECTED", "QUARANTINED", "REPAIRING", "REVALIDATING", "PUBLISHING"}:
        pipeline_health = "running"
    else:
        pipeline_health = "waiting"

    checks = []
    if isinstance(quality_report, dict):
        checks = quality_report.get("checks") or quality_report.get("core", {}).get("checks") or []
    freshness = _freshness(clean_rows)
    if latest:
        current_checks = latest.get("post_repair_checks") or latest.get("pre_repair_checks")
        if isinstance(current_checks, dict):
            freshness = current_checks.get("freshness") or freshness
        if "is_fresh" in freshness:
            freshness = {**freshness, "status": "healthy" if freshness["is_fresh"] else "alert"}

    completed_runs = [
        event
        for event in events
        if event.get("state") in {"HEALTHY", "REPAIR_FAILED"}
    ]
    drift_history = []
    for event in completed_runs:
        payload = event.get("payload") or {}
        drift_history.append(
            {
                "timestamp": event.get("timestamp"),
                "state": event.get("state"),
                "stale_ratio_before": payload.get("stale_ratio_before"),
                "stale_ratio_after": payload.get("stale_ratio_after"),
                "rows_after": payload.get("rows_after"),
            }
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "health": pipeline_health,
        "latest_state": latest_state,
        "quality": {
            "status": (
                "passed" if quality_report.get("success") is True else
                "failed" if quality_report.get("success") is False else
                "waiting"
            ) if isinstance(quality_report, dict) else "waiting",
            "report": quality_report,
            "checks": checks,
            "gx_status": (
                quality_report.get("gx", {}).get("status", "waiting")
                if isinstance(quality_report, dict)
                else "waiting"
            ),
        },
        "gx_artifacts": gx_artifacts,
        "freshness": freshness,
        "age_distribution": _age_distribution(clean_rows),
        "datasets": {
            "baseline": {"rows": len(clean_rows), "documents": counts[settings.baseline_collection_name]},
            "corrupted": {"rows": len(corrupted_rows), "documents": counts[settings.corrupted_collection_name]},
            "repaired": {"rows": len(repaired_rows), "documents": counts[settings.repaired_collection_name]},
        },
        "active_documents": (
            counts.get(latest.get("active_collection")) if latest else counts[settings.baseline_collection_name]
        ),
        "retrieval_metrics": _read_metrics(settings),
        "artifacts": _artifact_status(settings),
        "last_run": latest,
        "events": events,
        "drift_history": drift_history,
        "affected_paper_ids": latest.get("affected_paper_ids", []) if latest else [],
    }


def artifact_signature(settings: Settings) -> tuple[tuple[str, int | None], ...]:
    paths = (
        settings.paths.self_healing_latest_json,
        settings.paths.baseline_quality_report,
        settings.paths.corrupted_quality_report,
        settings.paths.freshness_report,
        settings.paths.baseline_metrics,
        settings.paths.corrupted_metrics,
        settings.paths.repaired_metrics,
        settings.paths.clean_json,
        settings.paths.repaired_clean_json,
    )
    signature = []
    for path in paths:
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = None
        signature.append((str(path), mtime))
    return tuple(signature)
