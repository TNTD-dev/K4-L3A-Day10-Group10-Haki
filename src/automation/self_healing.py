from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import threading
import uuid
from typing import Any, Literal

import chromadb
import pandas as pd

from automation.quality_gate import CoreQualityGate
from core.config import Settings
from core.utils import read_json, write_csv, write_json
from ingestion.cleaning import build_clean_dataframe
from ingestion.corruption import corrupt_clean_dataframe
from ingestion.crossref import PaperRecord, fetch_source_records, load_raw_records
from retrieval.index import LocalEmbeddingIndex


RepairSource = Literal["auto", "snapshot", "live"]
_EVENT_LOCK = threading.Lock()
_MUTATION_LOCK = threading.Lock()


@dataclass(frozen=True)
class SelfHealingResult:
    run_id: str
    state: str
    success: bool
    repair_source_requested: str
    repair_source_used: str | None
    rows_before: int | None
    rows_after: int | None
    error: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_error(error: Exception, settings: Settings) -> str:
    message = f"{type(error).__name__}: {error}"
    for secret in (
        settings.openai_api_key,
        settings.google_api_key,
        settings.anthropic_api_key,
        settings.openrouter_api_key,
        settings.custom_llm_api_key,
    ):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:1000]


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        write_json(temporary, payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_gx_result(df: pd.DataFrame, settings: Settings, report_name: str) -> dict[str, Any]:
    try:
        from observability.quality import run_data_quality_checks

        result = run_data_quality_checks(df, settings, report_name)
    except NotImplementedError:
        return {"status": "waiting", "success": None, "message": "GX quality checks are not implemented yet."}
    except Exception as exc:  # GX failures are gate failures, not silent skips.
        return {"status": "failed", "success": False, "error": _safe_error(exc, settings)}

    if not isinstance(result, dict):
        return {
            "status": "failed",
            "success": False,
            "error": f"GX quality adapter returned {type(result).__name__}, expected a dictionary.",
        }
    success = result.get("success", result.get("overall_success"))
    if success is None:
        return {"status": "failed", "success": False, "error": "GX result has no success flag.", "result": result}
    return {"status": "passed" if bool(success) else "failed", "success": bool(success), "result": result}


def _evaluate(df: pd.DataFrame, settings: Settings, report_name: str) -> dict[str, Any]:
    core = CoreQualityGate().evaluate(df)
    gx = _load_gx_result(df, settings, report_name)
    success = bool(core["success"] and gx.get("success") is not False)
    return {
        "generated_at": _now(),
        "success": success,
        "total_rows": len(df),
        "core": core,
        "gx": gx,
        "checks": core["checks"],
        "freshness": core["freshness"],
    }


def _json_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(df.to_json(orient="records", date_format="iso", force_ascii=True))


def _collection_names(client: Any) -> set[str]:
    return {collection.name for collection in client.list_collections()}


def _publish_repaired_artifacts(
    df: pd.DataFrame,
    settings: Settings,
    run_id: str,
) -> tuple[int, str]:
    paths = settings.paths
    suffix = run_id.replace("-", "")[:12]
    stage_collection = f"repaired-stage-{suffix}"
    backup_collection = f"repaired-backup-{suffix}"
    staged_files = {
        paths.repaired_clean_csv: paths.repaired_clean_csv.with_name(f".papers-repaired-{suffix}.csv"),
        paths.repaired_clean_json: paths.repaired_clean_json.with_name(f".papers-repaired-{suffix}.json"),
        paths.repaired_embeddings_json: paths.repaired_embeddings_json.with_name(
            f".papers-embeddings-repaired-{suffix}.json"
        ),
    }
    backup_files = {destination: destination.with_name(f".{destination.name}.{suffix}.bak") for destination in staged_files}

    for destination in (*staged_files.keys(), paths.chroma_dir):
        destination.mkdir(parents=True, exist_ok=True) if destination.suffix == "" else destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    client = chromadb.PersistentClient(path=str(paths.chroma_dir))
    names = _collection_names(client)
    if stage_collection in names or backup_collection in names:
        raise RuntimeError("A staging or backup collection with this run ID already exists.")

    stage_csv = staged_files[paths.repaired_clean_csv]
    stage_json = staged_files[paths.repaired_clean_json]
    stage_manifest = staged_files[paths.repaired_embeddings_json]
    try:
        write_csv(df, stage_csv)
        write_json(stage_json, _json_rows(df))
        LocalEmbeddingIndex.build(
            df,
            settings,
            stage_manifest,
            collection_name=stage_collection,
        )
        collection = client.get_collection(name=stage_collection)
        document_count = int(collection.count())
        if document_count != len(df):
            raise RuntimeError(f"Staging collection contains {document_count} documents for {len(df)} rows.")

        manifest = read_json(stage_manifest)
        manifest["collection_name"] = settings.repaired_collection_name
        _write_json_atomic(stage_manifest, manifest)

        old_collection_renamed = False
        new_collection_promoted = False
        replaced_destinations: list[Path] = []
        try:
            if settings.repaired_collection_name in _collection_names(client):
                client.get_collection(name=settings.repaired_collection_name).modify(name=backup_collection)
                old_collection_renamed = True
            client.get_collection(name=stage_collection).modify(name=settings.repaired_collection_name)
            new_collection_promoted = True

            for destination, staged in staged_files.items():
                if destination.exists():
                    shutil.copy2(destination, backup_files[destination])
                os.replace(staged, destination)
                replaced_destinations.append(destination)
        except Exception:
            for destination in reversed(replaced_destinations):
                backup = backup_files[destination]
                if backup.exists():
                    os.replace(backup, destination)
                else:
                    destination.unlink(missing_ok=True)
            current_names = _collection_names(client)
            if new_collection_promoted and settings.repaired_collection_name in current_names:
                client.get_collection(name=settings.repaired_collection_name).modify(name=stage_collection)
            if old_collection_renamed and backup_collection in _collection_names(client):
                client.get_collection(name=backup_collection).modify(name=settings.repaired_collection_name)
            raise

        if backup_collection in _collection_names(client):
            try:
                client.delete_collection(name=backup_collection)
            except Exception:
                # The canonical collection is already healthy; a stale backup is safe to inspect later.
                pass
        for backup in backup_files.values():
            backup.unlink(missing_ok=True)
        return document_count, settings.repaired_collection_name
    except Exception:
        if stage_collection in _collection_names(client):
            try:
                client.delete_collection(name=stage_collection)
            except Exception:
                pass
        for staged in staged_files.values():
            staged.unlink(missing_ok=True)
        raise


def _choose_source(
    requested: RepairSource,
    pre_report: dict[str, Any],
) -> Literal["snapshot", "live"]:
    if requested in {"snapshot", "live"}:
        return requested
    failed = [check["name"] for check in pre_report["core"]["checks"] if not check["success"]]
    if failed == ["freshness_sla"] and pre_report.get("gx", {}).get("success") is not False:
        return "live"
    return "snapshot"


def _load_repair_records(source: Literal["snapshot", "live"], settings: Settings) -> tuple[list[PaperRecord], str]:
    if source == "live":
        before_mtime = (
            settings.paths.raw_api_response.stat().st_mtime_ns
            if settings.paths.raw_api_response.exists()
            else None
        )
        records = fetch_source_records(replace(settings, refresh_source=True))
        after_mtime = (
            settings.paths.raw_api_response.stat().st_mtime_ns
            if settings.paths.raw_api_response.exists()
            else None
        )
        return records, "live" if after_mtime != before_mtime else "snapshot_fallback"

    try:
        return load_raw_records(settings.paths.raw_records_json), "snapshot"
    except (FileNotFoundError, ValueError):
        records = fetch_source_records(replace(settings, refresh_source=True))
        return records, "live_fallback"


def run_self_healing(
    settings: Settings,
    trigger: str,
    repair_source: RepairSource = "auto",
    *,
    run_id: str | None = None,
) -> SelfHealingResult:
    """Run a corruption drill, detect it, repair from raw lineage, and publish atomically."""
    if repair_source not in {"auto", "snapshot", "live"}:
        raise ValueError("repair_source must be one of: auto, snapshot, live.")
    if not _MUTATION_LOCK.acquire(blocking=False):
        raise RuntimeError("A self-healing run is already active in this process.")

    run_id = run_id or uuid.uuid4().hex
    started_at = _now()
    sequence = 0
    latest: dict[str, Any] = {
        "run_id": run_id,
        "trigger": trigger,
        "state": "IDLE",
        "started_at": started_at,
        "updated_at": started_at,
        "repair_source_requested": repair_source,
        "repair_source_used": None,
        "pre_repair_checks": None,
        "post_repair_checks": None,
        "affected_paper_ids": [],
        "row_count_before": None,
        "row_count_corrupted": None,
        "row_count_after": None,
        "active_collection": settings.baseline_collection_name,
        "error": None,
    }

    def record_event(
        state: str,
        message: str,
        severity: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> None:
        nonlocal sequence
        sequence += 1
        timestamp = _now()
        event = {
            "event_id": uuid.uuid4().hex,
            "run_id": run_id,
            "sequence": sequence,
            "timestamp": timestamp,
            "state": state,
            "severity": severity,
            "message": message,
            "payload": payload or {},
        }
        latest["state"] = state
        latest["updated_at"] = timestamp
        with _EVENT_LOCK:
            settings.paths.self_healing_events_jsonl.parent.mkdir(parents=True, exist_ok=True)
            with settings.paths.self_healing_events_jsonl.open("a", encoding="utf-8") as event_file:
                event_file.write(json.dumps(event, ensure_ascii=True) + "\n")
            _write_json_atomic(settings.paths.self_healing_latest_json, latest)

    rows_before: int | None = None
    rows_after: int | None = None
    source_used: str | None = None
    try:
        record_event("IDLE", "Failure drill accepted.", payload={"trigger": trigger})
        record_event("CORRUPTING", "Loading preserved raw records and injecting controlled faults.")

        records = load_raw_records(settings.paths.raw_records_json)
        clean_df = build_clean_dataframe(records, datetime.now(UTC))
        if clean_df.empty:
            raise ValueError("Raw snapshot produced an empty clean dataset.")
        rows_before = len(clean_df)
        latest["row_count_before"] = rows_before

        corrupted_df = corrupt_clean_dataframe(clean_df, settings.paths.corruption_log)
        rows_corrupted = len(corrupted_df)
        latest["row_count_corrupted"] = rows_corrupted
        write_csv(corrupted_df, settings.paths.corrupted_clean_csv)
        write_json(settings.paths.corrupted_clean_json, _json_rows(corrupted_df))

        pre_report = _evaluate(corrupted_df, settings, f"self_healing_pre_{run_id}")
        _write_json_atomic(settings.paths.pre_repair_quality_report, pre_report)
        latest["pre_repair_checks"] = pre_report
        corruption_log = read_json(settings.paths.corruption_log)
        affected_ids = sorted(
            {
                paper_id
                for scenario in corruption_log.get("scenarios", [])
                for paper_id in scenario.get("affected_paper_ids", [])
            }
        )
        latest["affected_paper_ids"] = affected_ids

        if pre_report["success"]:
            raise RuntimeError("The quality gate did not detect the injected corruption; repair was not triggered.")
        record_event(
            "DETECTED",
            "Quality gate detected injected data faults.",
            severity="warning",
            payload={
                "failed_checks": [check["name"] for check in pre_report["core"]["checks"] if not check["success"]],
                "gx_status": pre_report["gx"]["status"],
            },
        )

        record_event("QUARANTINED", "Corrupted dataset is isolated from the active repaired collection.")
        LocalEmbeddingIndex.build(
            corrupted_df,
            settings,
            settings.paths.corrupted_embeddings_json,
            collection_name=settings.corrupted_collection_name,
        )

        selected_source = _choose_source(repair_source, pre_report)
        record_event(
            "REPAIRING",
            "Rebuilding clean records from the selected raw source.",
            payload={"repair_source": selected_source},
        )
        repair_records, source_used = _load_repair_records(selected_source, settings)
        latest["repair_source_used"] = source_used
        repaired_df = build_clean_dataframe(repair_records, datetime.now(UTC))
        if repaired_df.empty:
            raise ValueError("Repair source produced an empty clean dataset.")

        post_report = _evaluate(repaired_df, settings, f"self_healing_post_{run_id}")
        _write_json_atomic(settings.paths.post_repair_quality_report, post_report)
        latest["post_repair_checks"] = post_report
        latest["row_count_after"] = len(repaired_df)
        rows_after = len(repaired_df)
        if not post_report["success"]:
            record_event(
                "REVALIDATING",
                "Rebuilt data failed the post-repair quality gate.",
                severity="error",
                payload={"failed_checks": [check["name"] for check in post_report["checks"] if not check["success"]]},
            )
            raise RuntimeError("Post-repair quality gate failed; repaired artifacts were not published.")

        record_event(
            "REVALIDATING",
            "Rebuilt data passed the post-repair quality gate.",
            payload={"rows": rows_after, "gx_status": post_report["gx"]["status"]},
        )
        record_event("PUBLISHING", "Building the repaired index in staging before promotion.")
        document_count, active_collection = _publish_repaired_artifacts(repaired_df, settings, run_id)
        latest["active_collection"] = active_collection
        latest["row_count_after"] = rows_after

        summary = {
            "success": True,
            "rows_before": rows_before,
            "rows_corrupted": rows_corrupted,
            "rows_after": rows_after,
            "documents_repaired": document_count,
            "stale_ratio_before": pre_report["freshness"]["stale_ratio"],
            "stale_ratio_after": post_report["freshness"]["stale_ratio"],
        }
        record_event("HEALTHY", "Repair passed quality checks and the repaired index is active.", payload=summary)
        latest["finished_at"] = _now()
        _write_json_atomic(settings.paths.self_healing_latest_json, latest)
        return SelfHealingResult(
            run_id=run_id,
            state="HEALTHY",
            success=True,
            repair_source_requested=repair_source,
            repair_source_used=source_used,
            rows_before=rows_before,
            rows_after=rows_after,
        )
    except Exception as exc:
        safe_message = _safe_error(exc, settings)
        latest["error"] = safe_message
        latest["repair_source_used"] = source_used
        latest["row_count_before"] = rows_before
        latest["row_count_after"] = rows_after
        record_event("REPAIR_FAILED", "Self-healing could not publish a validated repaired index.", "error", {"error": safe_message})
        latest["finished_at"] = _now()
        _write_json_atomic(settings.paths.self_healing_latest_json, latest)
        return SelfHealingResult(
            run_id=run_id,
            state="REPAIR_FAILED",
            success=False,
            repair_source_requested=repair_source,
            repair_source_used=source_used,
            rows_before=rows_before,
            rows_after=rows_after,
            error=safe_message,
        )
    finally:
        _MUTATION_LOCK.release()
