from __future__ import annotations

from typing import Any

import pandas as pd

from core.utils import first_sentence, write_json

TEST_SET_SIZE = 10
# 3 summary / 3 authors / 2 date / 2 categories
QUESTION_TYPES = ["summary", "authors", "date", "categories"] * 2 + ["summary", "authors"]

# Wording phai khop `retrieval.qa._extract_answer` ("who authored", "when was", "what categories")
# va title dat trong '...' de qa lookup chinh xac.
TEMPLATES = {
    "summary": ("What is the summary of the paper '{title}'?", lambda row: first_sentence(str(row["summary"]))),
    "authors": ("Who authored the paper '{title}'?", lambda row: str(row["authors_joined"] or "")),
    "date": ("When was the paper '{title}' published?", lambda row: str(row["published"])[:10]),
    "categories": ("What categories does the paper '{title}' belong to?", lambda row: str(row["categories_joined"] or "")),
}


def build_test_set(df: pd.DataFrame, output_path) -> list[dict[str, Any]]:
    """Sinh 10 cau hoi tat dinh (cung input -> cung output) phu toi da 4 loai nghiep vu.

    Chi hoi cau CO dap an: loai nao khong co paper nao co du lieu (vd Crossref khong tra `subject`)
    thi thay bang loai khac va canh bao, tranh ground truth rong lam sai metric.
    """
    candidates = df.drop_duplicates("paper_id")
    # Title co dau ' se pha regex lookup trong qa.py.
    candidates = candidates[~candidates["title"].astype(str).str.contains("'")]
    if len(candidates) < TEST_SET_SIZE:
        raise ValueError(f"Can it nhat {TEST_SET_SIZE} papers hop le de tao test set, hien co {len(candidates)}.")

    # Rai deu theo ngay xuat ban (moi -> cu) de test set cham ca bai moi nhat lan bai cu.
    ordered = candidates.sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)
    rows = ordered.to_dict("records")
    step = len(rows) / TEST_SET_SIZE
    spread = [int(i * step) for i in range(TEST_SET_SIZE)]

    answerable = {kind: any(answer(r).strip() for r in rows) for kind, (_, answer) in TEMPLATES.items()}
    fallback = [kind for kind in ("summary", "authors", "date") if answerable[kind]]
    skipped = sorted(kind for kind, ok in answerable.items() if not ok)
    if skipped:
        print(f"[testset] WARNING: khong co du lieu cho loai {skipped} -> thay bang {fallback}")

    used: set[int] = set()
    test_set = []
    for i, kind in enumerate(QUESTION_TYPES):
        if not answerable[kind]:
            kind = fallback[i % len(fallback)]
        template, answer = TEMPLATES[kind]
        # Uu tien vi tri rai deu, thieu thi lay paper ke tiep co dap an khong rong.
        order = [spread[i]] + [j for j in range(len(rows)) if j != spread[i]]
        pos = next(j for j in order if j not in used and answer(rows[j]).strip())
        used.add(pos)
        row = rows[pos]
        test_set.append(
            {
                "id": f"eval_{i + 1:03d}",
                "question_type": kind,
                "question": template.format(title=row["title"]),
                "ground_truth": answer(row),
                "ground_truth_doc_ids": [row["paper_id"]],
            }
        )
    write_json(output_path, test_set)
    return test_set
