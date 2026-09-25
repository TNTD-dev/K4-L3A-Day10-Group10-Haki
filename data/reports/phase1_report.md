# Phase 1 Report — Baseline Pipeline

_Generated: 2026-09-25T08:54:45.517651+00:00_

## 1. Source & Lineage

| Field | Value |
|---|---|
| source | Crossref REST API |
| query | agentic retrieval augmented generation large language model |
| live_refresh | ✅ PASS |
| raw_records | 24 |
| clean_rows | 24 |
| published_range | 2026-04-01 → 2026-09-15 |
| run_date | 2026-09-25 |
| embedding | openai/text-embedding-3-small |
| llm | openai/gpt-5.6-luna |
| collection | papers-baseline |
| test_questions | 10 |

## 2. RAG Baseline Metrics

| Metric | Value |
|---|---|
| Retrieval Hit Rate | 1.0000 |
| Mean Token F1 | 1.0000 |
| LLM Judge Accuracy | 1.0000 |
| Mean Judge Score (1-5) | 5 |
| Samples | 10 |

## 3. Data Quality Gate (great_expectations 1.18.0)

**Gate:** ✅ PASS — 18 expectations, critical failed: none, warnings: expect_column_value_lengths_to_be_between(categories_joined) · fingerprint `cac0b8b1a956cb10`

| Pillar | Expectation | Column | Severity | Result | Unexpected |
|---|---|---|---|---|---|
| schema | expect_column_to_exist | paper_id | critical | ✅ PASS | 0 |
| completeness | expect_column_values_to_not_be_null | paper_id | critical | ✅ PASS | 0 |
| uniqueness | expect_column_values_to_be_unique | paper_id | critical | ✅ PASS | 0 |
| schema | expect_column_to_exist | title | critical | ✅ PASS | 0 |
| completeness | expect_column_values_to_not_be_null | title | critical | ✅ PASS | 0 |
| distribution | expect_column_value_lengths_to_be_between | title | critical | ✅ PASS | 0 |
| schema | expect_column_to_exist | summary | critical | ✅ PASS | 0 |
| distribution | expect_column_value_lengths_to_be_between | summary | critical | ✅ PASS | 0 |
| schema | expect_column_to_exist | authors_joined | critical | ✅ PASS | 0 |
| completeness | expect_column_value_lengths_to_be_between | authors_joined | warning | ✅ PASS | 0 |
| schema | expect_column_to_exist | categories_joined | critical | ✅ PASS | 0 |
| completeness | expect_column_value_lengths_to_be_between | categories_joined | warning | ❌ FAIL | 24 |
| schema | expect_column_to_exist | published | critical | ✅ PASS | 0 |
| schema | expect_column_to_exist | age_days | critical | ✅ PASS | 0 |
| freshness | expect_column_values_to_be_between | age_days | warning | ✅ PASS | 0 |
| schema | expect_column_to_exist | text_for_embedding | critical | ✅ PASS | 0 |
| completeness | expect_column_values_to_not_be_null | text_for_embedding | critical | ✅ PASS | 0 |
| volume | expect_table_row_count_to_be_between | table | critical | ✅ PASS | 0 |

## 4. Freshness SLA (age_days > 180 ≤ 25% rows)

| Field | Value |
|---|---|
| total_rows | 24 |
| stale_rows | 0 |
| stale_ratio | 0.0000 |
| latest_published | 2026-09-15 |
| oldest_published | 2026-04-01 |
| latest_age_days | 10 |
| is_fresh | ✅ PASS |
