from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ingestion.cleaning import CLEAN_COLUMNS
from ingestion.corruption import NOISE_MARKER as CORRUPTION_NOISE_MARKER


class CoreQualityGate:
    """Small, dependency-light checks required by the self-healing controller."""

    REQUIRED_COLUMNS = set(CLEAN_COLUMNS)
    NOISE_MARKER = CORRUPTION_NOISE_MARKER

    def evaluate(self, df: pd.DataFrame) -> dict[str, Any]:
        missing = sorted(self.REQUIRED_COLUMNS - set(df.columns))
        row_count = len(df)
        checks: list[dict[str, Any]] = []

        def add(name: str, success: bool, observed: Any, expected: str) -> None:
            checks.append(
                {
                    "name": name,
                    "success": bool(success),
                    "observed": observed,
                    "expected": expected,
                }
            )

        add(
            "schema",
            not missing,
            {"columns": list(df.columns), "missing": missing},
            "required quality columns are present",
        )
        add("row_count", 5 <= row_count <= 5000, row_count, "5 <= rows <= 5000")

        for column in ("paper_id", "title", "text_for_embedding"):
            if column not in df.columns:
                add(f"{column}_not_empty", False, None, "all values are non-null and non-empty")
                continue
            values = df[column]
            valid = values.notna() & values.astype("string").str.strip().ne("")
            add(
                f"{column}_not_empty",
                bool(valid.all()),
                {"invalid_rows": int((~valid).sum())},
                "all values are non-null and non-empty",
            )

        if "paper_id" in df.columns:
            ids = df["paper_id"].astype("string").str.strip().str.casefold()
            duplicate_count = int(ids.duplicated(keep=False).sum())
            add("paper_id_unique", duplicate_count == 0, {"duplicate_rows": duplicate_count}, "unique DOI per row")
        else:
            add("paper_id_unique", False, None, "unique DOI per row")

        if "summary" in df.columns:
            lengths = df["summary"].fillna("").astype("string").str.len()
            short_count = int(lengths.lt(30).sum())
            add("summary_min_length", short_count == 0, {"short_rows": short_count}, "summary length >= 30")
        else:
            add("summary_min_length", False, None, "summary length >= 30")

        if "title" in df.columns:
            lengths = df["title"].fillna("").astype("string").str.len()
            short_count = int(lengths.lt(8).sum())
            add("title_min_length", short_count == 0, {"short_rows": short_count}, "title length >= 8")
        else:
            add("title_min_length", False, None, "title length >= 8")

        searchable_columns = [column for column in ("title", "summary", "text_for_embedding") if column in df.columns]
        if searchable_columns:
            contains_noise = pd.Series(False, index=df.index)
            for column in searchable_columns:
                contains_noise |= df[column].fillna("").astype("string").str.contains(
                    self.NOISE_MARKER,
                    regex=False,
                    na=False,
                )
            noise_rows = int(contains_noise.sum())
            add("noise_marker_absent", noise_rows == 0, {"affected_rows": noise_rows}, "no injected noise marker")
        else:
            add("noise_marker_absent", False, None, "no injected noise marker")

        if "age_days" in df.columns and row_count:
            ages = pd.to_numeric(df["age_days"], errors="coerce")
            valid_age_count = int(ages.notna().sum())
            stale_count = int(ages.gt(180).sum())
            stale_ratio = stale_count / row_count
            freshness_success = valid_age_count == row_count and stale_ratio <= 0.25
            freshness = {
                "stale_rows": stale_count,
                "total_rows": row_count,
                "stale_ratio": stale_ratio,
                "threshold_days": 180,
                "max_stale_ratio": 0.25,
                "invalid_age_rows": row_count - valid_age_count,
                "is_fresh": freshness_success,
            }
            add(
                "freshness_sla",
                freshness_success,
                freshness,
                "age_days > 180 ratio <= 25% and all ages are numeric",
            )
        else:
            freshness = {
                "stale_rows": None,
                "total_rows": row_count,
                "stale_ratio": None,
                "threshold_days": 180,
                "max_stale_ratio": 0.25,
                "invalid_age_rows": row_count,
                "is_fresh": False,
            }
            add("freshness_sla", False, freshness, "age_days > 180 ratio <= 25%")

        return {
            "source": "core",
            "generated_at": datetime.now(UTC).isoformat(),
            "total_rows": row_count,
            "success": all(check["success"] for check in checks),
            "checks": checks,
            "freshness": freshness,
        }
