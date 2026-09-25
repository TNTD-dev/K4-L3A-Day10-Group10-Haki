# Corruption & Repair Report — Baseline vs Corrupted vs Repaired

_Generated: 2026-09-25T08:55:57.951788+00:00_

## 1. RAG Metrics (3 trạng thái)

| Metric | Baseline | Corrupted | Repaired | Δ Corrupted | Δ Repaired |
|---|---|---|---|---|---|
| Retrieval Hit Rate | 1.0000 | 0.7000 | 1.0000 | -0.3000 | +0.0000 |
| Mean Token F1 | 1.0000 | 0.3201 | 1.0000 | -0.6799 | +0.0000 |
| LLM Judge Accuracy | 1.0000 | 0.5000 | 1.0000 | -0.5000 | +0.0000 |
| Mean Judge Score (1-5) | 5 | 3 | 5 | -2.0000 | +0.0000 |

**Recovery verdict:** ✅ Repaired khớp Baseline (tolerance ±0.01).

## 2. Data Quality Gate (GX 1.x)

|  | Corrupted | Repaired |
|---|---|---|
| Overall | ❌ FAIL | ✅ PASS |
| Rows / unique papers | 22 / 19 | 24 / 24 |
| Critical failed | expect_column_unique_value_count_to_be_between(paper_id), expect_column_values_to_be_unique(paper_id), expect_column_value_lengths_to_be_between(title), expect_column_value_lengths_to_be_between(summary) | none |
| Warnings | expect_column_value_lengths_to_be_between(categories_joined), expect_column_values_to_be_between(age_days) | expect_column_value_lengths_to_be_between(categories_joined) |
| Data fingerprint | 10569fde7b8cb1c9 | cac0b8b1a956cb10 |

### Chi tiết expectation (Corrupted)

| Pillar | Expectation | Column | Severity | Result | Unexpected |
|---|---|---|---|---|---|
| schema | expect_column_to_exist | paper_id | critical | ✅ PASS | 0 |
| volume | expect_column_unique_value_count_to_be_between | paper_id | critical | ❌ FAIL | 0 |
| completeness | expect_column_values_to_not_be_null | paper_id | critical | ✅ PASS | 0 |
| uniqueness | expect_column_values_to_be_unique | paper_id | critical | ❌ FAIL | 6 |
| schema | expect_column_to_exist | title | critical | ✅ PASS | 0 |
| completeness | expect_column_values_to_not_be_null | title | critical | ✅ PASS | 0 |
| distribution | expect_column_value_lengths_to_be_between | title | critical | ❌ FAIL | 2 |
| schema | expect_column_to_exist | summary | critical | ✅ PASS | 0 |
| distribution | expect_column_value_lengths_to_be_between | summary | critical | ❌ FAIL | 2 |
| schema | expect_column_to_exist | authors_joined | critical | ✅ PASS | 0 |
| completeness | expect_column_value_lengths_to_be_between | authors_joined | warning | ✅ PASS | 0 |
| schema | expect_column_to_exist | categories_joined | critical | ✅ PASS | 0 |
| completeness | expect_column_value_lengths_to_be_between | categories_joined | warning | ❌ FAIL | 22 |
| schema | expect_column_to_exist | published | critical | ✅ PASS | 0 |
| schema | expect_column_to_exist | age_days | critical | ✅ PASS | 0 |
| freshness | expect_column_values_to_be_between | age_days | warning | ❌ FAIL | 7 |
| schema | expect_column_to_exist | text_for_embedding | critical | ✅ PASS | 0 |
| completeness | expect_column_values_to_not_be_null | text_for_embedding | critical | ✅ PASS | 0 |
| volume | expect_table_row_count_to_be_between | table | critical | ✅ PASS | 0 |

## 3. Freshness SLA

| Field | Corrupted | Repaired |
|---|---|---|
| total_rows | 22 | 24 |
| stale_rows | 7 | 0 |
| stale_ratio | 0.3182 | 0.0000 |
| latest_published | 2026-08-27 | 2026-09-15 |
| latest_age_days | 29 | 10 |
| is_fresh | ❌ FAIL | ✅ PASS |

## 4. Corruption Impact (24 → 22 rows)

| Corruption | Rows | Benchmark papers hit | Detector | Observed |
|---|---|---|---|---|
| drop_latest_records | 5 | 3 | GX volume ExpectColumnUniqueValueCountToBeBetween(paper_id >= 90% baseline) | ✅ detected |
| blank_summary | 2 | 1 | GX ExpectColumnValueLengthsToBeBetween(summary >= 30) | ✅ detected |
| inject_noise | 2 | 2 | Khong co (silent) - chi lo qua Token F1 | ⚠️ silent |
| truncate_title | 2 | 2 | GX ExpectColumnValueLengthsToBeBetween(title >= 8) + exact lookup fail | ✅ detected |
| stale_date | 7 | 2 | Freshness SLA is_fresh=False (warning) | ✅ detected |
| duplicate_rows | 3 | 0 | GX ExpectColumnValuesToBeUnique(paper_id) | ✅ detected |

_Targeted injection: lỗi được nhắm vào các paper có trong test set để tác động đo được; danh sách paper bị ảnh hưởng nằm trong `corruption_log.json`._

## 4b. Metrics theo loại câu hỏi (hit rate / token F1)

| Question type | Baseline | Corrupted | Repaired |
|---|---|---|---|
| authors | 1.00 / 1.00 | 0.75 / 0.75 | 1.00 / 1.00 |
| date | 1.00 / 1.00 | 0.50 / 0.00 | 1.00 / 1.00 |
| summary | 1.00 / 1.00 | 0.75 / 0.05 | 1.00 / 1.00 |

## 5. Auto-Repair

| Field | Value |
|---|---|
| triggered | yes |
| trigger_reasons | expect_column_unique_value_count_to_be_between(paper_id), expect_column_values_to_be_unique(paper_id), expect_column_value_lengths_to_be_between(title), expect_column_value_lengths_to_be_between(summary), freshness_sla |
| source | crossref_records.json |
| repaired_gate_pass | yes |
| baseline_fingerprint | cac0b8b1a956cb10 |
| repaired_fingerprint | cac0b8b1a956cb10 |
| fingerprint_match | yes |

## Analysis

- Quality Gate chặn được bộ dữ liệu corrupted; Freshness SLA: vi phạm.

- Lỗi lọt qua kiểm tra cấu trúc (silent): inject_noise → chỉ lộ ra qua RAG metrics.

- Repair dựng lại từ raw snapshot bất biến (idempotent): chạy N lần cho cùng kết quả, không vá tay dữ liệu hỏng.
