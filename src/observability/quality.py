from __future__ import annotations

import hashlib
import math
from typing import Any

import great_expectations as gx
import great_expectations.expectations as gxe
import pandas as pd

from core.config import Settings
from core.utils import now_utc, safe_slug, write_json

# Contract voi pipeline RAG: clean dataframe phai co it nhat cac cot nay.
REQUIRED_COLUMNS = [
    "paper_id",
    "title",
    "summary",
    "authors_joined",
    "categories_joined",
    "published",
    "age_days",
    "text_for_embedding",
]
MIN_ROWS, MAX_ROWS = 5, 5000
MIN_SUMMARY_CHARS = 30
MIN_TITLE_CHARS = 8
MAX_STALE_RATIO = 0.25


def quality_report_path(settings: Settings, report_name: str):
    return settings.paths.quality_dir / f"{safe_slug(report_name)}_quality_report.json"


def freshness_report_path(settings: Settings, report_name: str):
    if report_name == "baseline":
        return settings.paths.freshness_report
    return settings.paths.quality_dir / f"{safe_slug(report_name)}_freshness_report.json"


MIN_METADATA_COVERAGE = 0.8
MIN_VOLUME_RATIO = 0.9  # mat > 10% paper so voi lan chay tham chieu -> volume anomaly


class DataQualityError(RuntimeError):
    """Gate critical fail: khong duoc dua dataset nay vao vector store."""


def _meta(pillar: str, severity: str = "critical") -> dict[str, str]:
    return {"severity": severity, "pillar": pillar}


def _build_suite(name: str, freshness_days: int, columns: list[str], reference_unique_ids: int | None) -> gx.ExpectationSuite:
    """Suite theo 5 pillar cua data observability: schema, volume, distribution, freshness (+ lineage o fingerprint).

    critical -> chan index (gate FAIL). warning -> chi canh bao (Freshness SLA theo Guide).
    """
    suite = gx.ExpectationSuite(name=name)
    # Schema: thieu cot -> fail ro rang thay vi crash.
    for column in REQUIRED_COLUMNS:
        suite.add_expectation(gxe.ExpectColumnToExist(column=column, meta=_meta("schema")))
    # Volume
    suite.add_expectation(gxe.ExpectTableRowCountToBeBetween(min_value=MIN_ROWS, max_value=MAX_ROWS, meta=_meta("volume")))
    if reference_unique_ids and "paper_id" in columns:
        suite.add_expectation(
            gxe.ExpectColumnUniqueValueCountToBeBetween(
                column="paper_id", min_value=math.ceil(reference_unique_ids * MIN_VOLUME_RATIO), meta=_meta("volume")
            )
        )
    # Completeness / uniqueness / distribution
    for column in ("paper_id", "title", "text_for_embedding"):
        if column in columns:
            suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column=column, meta=_meta("completeness")))
    if "paper_id" in columns:
        suite.add_expectation(gxe.ExpectColumnValuesToBeUnique(column="paper_id", meta=_meta("uniqueness")))
    if "summary" in columns:
        suite.add_expectation(gxe.ExpectColumnValueLengthsToBeBetween(column="summary", min_value=MIN_SUMMARY_CHARS, meta=_meta("distribution")))
    if "title" in columns:
        suite.add_expectation(gxe.ExpectColumnValueLengthsToBeBetween(column="title", min_value=MIN_TITLE_CHARS, meta=_meta("distribution")))
    # Metadata phuc vu cau hoi authors/categories: rong nhieu -> RAG khong tra loi duoc (warning, nguon co the thieu).
    for column in ("authors_joined", "categories_joined"):
        if column in columns:
            suite.add_expectation(
                gxe.ExpectColumnValueLengthsToBeBetween(column=column, min_value=1, mostly=MIN_METADATA_COVERAGE, meta=_meta("completeness", "warning"))
            )
    # Freshness SLA: toi thieu 75% bai <= 180 ngay. Guide: chi "gan co canh bao" -> warning.
    if "age_days" in columns:
        suite.add_expectation(
            gxe.ExpectColumnValuesToBeBetween(
                column="age_days", min_value=0, max_value=freshness_days, mostly=1 - MAX_STALE_RATIO,
                meta=_meta("freshness", "warning"),
            )
        )
    return suite


def data_fingerprint(df: pd.DataFrame) -> str:
    """Lineage/idempotency: hash noi dung (bo age_days vi phu thuoc ngay chay), khong phu thuoc thu tu dong."""
    cols = sorted(c for c in df.columns if c != "age_days")
    canonical = df[cols].astype(str).sort_values(cols).to_json(orient="values")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def enforce_quality_gate(report: dict[str, Any]) -> None:
    """Goi truoc khi index/serve. Raise neu co expectation critical fail."""
    if not report["success"]:
        raise DataQualityError(f"Quality gate '{report['report_name']}' FAIL: {report['failed_expectations']}")


def _summarize_result(item: Any) -> dict[str, Any]:
    config = item.expectation_config
    kwargs = {k: v for k, v in dict(config.kwargs).items() if k != "batch_id"}
    result = item.result or {}
    meta = dict(config.meta or {})
    return {
        "expectation": config.type,
        "column": kwargs.get("column"),
        "severity": meta.get("severity", "critical"),
        "pillar": meta.get("pillar", ""),
        "kwargs": kwargs,
        "success": bool(item.success),
        "observed_value": result.get("observed_value"),
        "unexpected_count": int(result.get("unexpected_count") or 0),
        "unexpected_percent": float(result.get("unexpected_percent") or 0.0),
        "sample_unexpected": [str(v)[:80] for v in (result.get("partial_unexpected_list") or [])[:5]],
    }


def run_data_quality_checks(
    df: pd.DataFrame, settings: Settings, report_name: str, reference_unique_ids: int | None = None
) -> dict[str, Any]:
    """Quality Gate chuan GX 1.x. Khong raise: tra report; muon chan thi goi `enforce_quality_gate`.

    `success` = moi expectation critical pass (warning nhu Freshness khong lam fail gate).
    `reference_unique_ids`: so paper cua lan chay tham chieu (baseline) -> bat volume anomaly.
    """
    context = gx.get_context(mode="ephemeral")
    data_source = context.data_sources.add_pandas(name="papers_source")
    data_asset = data_source.add_dataframe_asset(name="papers_asset")
    batch_def = data_asset.add_batch_definition_whole_dataframe("papers_batch")
    batch = batch_def.get_batch(batch_parameters={"dataframe": df})

    suite = context.suites.add(
        _build_suite(
            f"papers_{safe_slug(report_name)}_suite", settings.freshness_threshold_days, list(df.columns), reference_unique_ids
        )
    )
    validation = batch.validate(suite)

    results = [_summarize_result(item) for item in validation.results]

    def failed(severity: str) -> list[str]:
        return [r["expectation"] + (f"({r['column']})" if r["column"] else "") for r in results if not r["success"] and r["severity"] == severity]

    report = {
        "report_name": report_name,
        "run_time": now_utc().isoformat(),
        "engine": f"great_expectations {gx.__version__}",
        "success": not failed("critical"),
        "gx_suite_success": bool(validation.success),
        "row_count": int(len(df)),
        "unique_papers": int(df["paper_id"].nunique()) if "paper_id" in df.columns else 0,
        "data_fingerprint": data_fingerprint(df),
        "evaluated_expectations": len(results),
        "failed_expectations": failed("critical"),
        "warnings": failed("warning"),
        "results": results,
    }
    write_json(quality_report_path(settings, report_name), report)
    return report


def build_freshness_report(df: pd.DataFrame, settings: Settings, report_path) -> dict[str, Any]:
    threshold = settings.freshness_threshold_days
    total = int(len(df))
    published = pd.to_datetime(df["published"], errors="coerce") if total else pd.Series(dtype="datetime64[ns]")
    ages = pd.to_numeric(df["age_days"], errors="coerce") if total else pd.Series(dtype=float)
    stale_rows = int((ages > threshold).sum())
    stale_ratio = stale_rows / total if total else 1.0

    report = {
        "run_time": now_utc().isoformat(),
        "threshold_days": threshold,
        "max_stale_ratio": MAX_STALE_RATIO,
        "total_rows": total,
        "stale_rows": stale_rows,
        "stale_ratio": round(stale_ratio, 4),
        "latest_published": published.max().strftime("%Y-%m-%d") if published.notna().any() else None,
        "oldest_published": published.min().strftime("%Y-%m-%d") if published.notna().any() else None,
        # Bai moi nhat da bao nhieu ngay: phat hien "mat du lieu tuoi" ma stale_ratio khong thay.
        "latest_age_days": int(ages.min()) if ages.notna().any() else None,
        "median_age_days": float(ages.median()) if ages.notna().any() else None,
        "age_days": [int(a) for a in ages.dropna()],
        "is_fresh": bool(total > 0 and stale_ratio <= MAX_STALE_RATIO),
    }
    write_json(report_path, report)
    return report
