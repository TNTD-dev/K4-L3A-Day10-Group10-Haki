from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from core.utils import now_utc, write_json
from ingestion.cleaning import build_embedding_text

DROP_LATEST_RATIO = 0.2
STALE_RATIO = 0.35
STALE_SHIFT_DAYS = 365
TRUNCATE_CHARS = 6
NOISE_MARKER = "~#"
COUNTS = {"blank_summary": 2, "inject_noise": 2, "truncate_title": 2, "duplicate_rows": 3}


def inject_noise(text: str) -> str:
    # Chen rac vao GIUA tu (ca cau dau) -> token that bi pha, F1 sap; do dai van hop le -> GX khong bat (silent).
    return re.sub(r"(\w{3})(?=\w)", rf"\1{NOISE_MARKER}", text)


def _ids_by_type(test_set: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in test_set or []:
        grouped.setdefault(item["question_type"], []).extend(str(pid) for pid in item["ground_truth_doc_ids"])
    return grouped


def _pick(pool: list[str], preferred: list[str], k: int, used: set[str]) -> list[str]:
    """Uu tien paper nam trong benchmark (de tac dong do duoc), thieu thi lay tiep theo paper_id."""
    chosen = [pid for pid in preferred if pid in pool and pid not in used][:k]
    chosen += [pid for pid in pool if pid not in used and pid not in chosen][: k - len(chosen)]
    used.update(chosen)
    return chosen


def _rebuild_derived(data: pd.DataFrame) -> None:
    data["text_for_embedding"] = [
        build_embedding_text(r["title"], r["authors_joined"], r["published"], r["categories_joined"], r["summary"])
        for r in data.to_dict("records")
    ]
    if "summary_chars" in data.columns:
        data["summary_chars"] = data["summary"].str.len()


def corrupt_clean_dataframe(
    df: pd.DataFrame, output_log_path: Path | str, test_set: list[dict[str, Any]] | None = None
) -> pd.DataFrame:
    """Tiem 6 loai loi co kiem soat, tat dinh (khong random, khong phu thuoc thu tu dong input).

    `test_set` (tuy chon): targeted injection - moi loi nham vao paper cua loai cau hoi ma no pha
    (blank/noise -> summary, stale -> date, truncate -> authors/categories). Khong co -> chon theo paper_id.
    """
    input_rows = len(df)
    data = df.copy(deep=True)
    data["paper_id"] = data["paper_id"].astype(str)
    data["published"] = pd.to_datetime(data["published"]).dt.strftime("%Y-%m-%d")
    targets = _ids_by_type(test_set)
    benchmark_ids = {pid for ids in targets.values() for pid in ids}
    scenarios: list[dict[str, Any]] = []

    def record(name: str, description: str, ids: list[str], detector: str, parameters: dict, before: int) -> None:
        scenarios.append({
            "name": name,
            "description": description,
            "affected_count": len(ids),
            "affected_paper_ids": ids,
            "benchmark_hits": [pid for pid in ids if pid in benchmark_ids],
            "parameters": parameters,
            "expected_detector": detector,
            "row_count_before": before,
            "row_count_after": len(data),
        })

    # 1. Drop latest 20%: ingestion bo sot du lieu moi.
    before = len(data)
    k = min(max(0, before - 1), math.ceil(before * DROP_LATEST_RATIO))
    latest = data.sort_values(["published", "paper_id"], ascending=[False, True], kind="mergesort").head(k)["paper_id"].tolist()
    data = data[~data["paper_id"].isin(latest)].sort_values("paper_id", kind="mergesort").reset_index(drop=True)
    record("drop_latest_records", f"Bo {k} bai moi nhat.", latest,
           "GX volume ExpectColumnUniqueValueCountToBeBetween(paper_id >= 90% baseline)", {"fraction": DROP_LATEST_RATIO}, before)

    pool = data["paper_id"].tolist()
    used: set[str] = set()
    summary_ids = targets.get("summary", [])

    # 2. Blank summary.
    ids = _pick(pool, summary_ids[0::2], COUNTS["blank_summary"], used)
    data.loc[data["paper_id"].isin(ids), "summary"] = ""
    record("blank_summary", "Xoa rong summary.", ids,
           "GX ExpectColumnValueLengthsToBeBetween(summary >= 30)", {}, len(data))

    # 3. Inject noise.
    ids = _pick(pool, summary_ids[1::2], COUNTS["inject_noise"], used)
    mask = data["paper_id"].isin(ids)
    data.loc[mask, "summary"] = data.loc[mask, "summary"].map(inject_noise)
    record("inject_noise", f"Chen '{NOISE_MARKER}' vao giua cac tu cua summary.", ids,
           "Khong co (silent) - chi lo qua Token F1", {"marker": NOISE_MARKER}, len(data))

    # 4. Truncate title < 8 ky tu.
    ids = _pick(pool, targets.get("authors", []) + targets.get("categories", []), COUNTS["truncate_title"], used)
    mask = data["paper_id"].isin(ids)
    data.loc[mask, "title"] = data.loc[mask, "title"].str[:TRUNCATE_CHARS]
    record("truncate_title", f"Cat title con {TRUNCATE_CHARS} ky tu.", ids,
           "GX ExpectColumnValueLengthsToBeBetween(title >= 8) + exact lookup fail", {"max_characters": TRUNCATE_CHARS}, len(data))

    # 5. Stale date: lui 365 ngay tren ~35% dong -> vuot SLA 25%.
    ids = _pick(pool, targets.get("date", []), max(1, round(len(pool) * STALE_RATIO)), used)
    mask = data["paper_id"].isin(ids)
    shifted = pd.to_datetime(data.loc[mask, "published"]) - pd.Timedelta(days=STALE_SHIFT_DAYS)
    data.loc[mask, "published"] = shifted.dt.strftime("%Y-%m-%d")
    data.loc[mask, "age_days"] = data.loc[mask, "age_days"] + STALE_SHIFT_DAYS
    record("stale_date", f"Lui published {STALE_SHIFT_DAYS} ngay.", ids,
           "Freshness SLA is_fresh=False (warning)", {"days_subtracted": STALE_SHIFT_DAYS, "fraction": STALE_RATIO}, len(data))

    # 6. Duplicate rows (tu cac dong chua bi dung).
    before = len(data)
    ids = _pick(pool, [], COUNTS["duplicate_rows"], used)
    data = pd.concat([data, data[data["paper_id"].isin(ids)]], ignore_index=True)
    record("duplicate_rows", f"Nhan ban {len(ids)} dong.", ids,
           "GX ExpectColumnValuesToBeUnique(paper_id)", {"duplicate_count": len(ids)}, before)

    # 7. Rebuild cot phu thuoc de loi "chay" xuong embedding.
    _rebuild_derived(data)

    write_json(Path(output_log_path), {
        "version": 2,
        "generated_at": now_utc().isoformat(),
        "targeted_injection": bool(test_set),
        "input_rows": input_rows,
        "output_rows": len(data),
        "scenarios": scenarios,
    })
    return data
