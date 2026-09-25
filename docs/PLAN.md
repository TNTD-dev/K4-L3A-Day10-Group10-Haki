# PLAN — Day 10 Data Pipeline & Observability (Group10-Haki)

> Checklist làm việc của nhóm. Tick `[x]` khi xong + commit. Số liệu trong report PHẢI sinh từ pipeline thật.

## 0. Interface đã có sẵn (KHÔNG sửa, code phải khớp)

| Thứ | Giá trị / chữ ký | Hệ quả khi code |
|---|---|---|
| `PaperRecord` | `paper_id, title, summary, authors[list], categories[list], primary_category, published, updated, abs_url, pdf_url, comment` | `crossref_records.json` đã đúng schema này → `load_raw_records` chỉ cần `PaperRecord(**d)` |
| Raw response | `message.items[]`: `DOI`, `title[0]`, `abstract` (có `<jats:p>`), `author[{given,family}]`, `subject[]`, `published.date-parts`, `created.date-time`, `URL` | parse phải strip JATS tag |
| `LocalEmbeddingIndex._build_documents` | cần cột `paper_id, title, text_for_embedding, published, authors_joined, categories_joined, summary, abs_url, pdf_url` | Chroma metadata không nhận list/None/Timestamp → `published` giữ **string** `YYYY-MM-DD` |
| `LocalEmbeddingIndex.build(df, settings, embeddings_output_path)` | path → collection: `embeddings_json`→baseline, `corrupted_embeddings_json`→corrupted, `repaired_embeddings_json`→repaired | truyền đúng path là ra đúng 3 collection |
| `qa._extract_answer` | nhận dạng câu hỏi theo cụm từ: `who authored` / `when was` / `what categories`; còn lại → `first_sentence(summary)` | **Câu hỏi testset phải chứa đúng các cụm này**, title đặt trong `'...'` để exact lookup |
| Ground truth | authors=`authors_joined`, date=`published`, categories=`categories_joined`, summary=`first_sentence(summary)` | khớp thì baseline F1 ≈ 1.0 |
| `evaluate_pipeline(settings, index, test_set_path, metrics_output_path, answers_output_path)` | trả `EvaluationBundle.summary` (`retrieval_hit_rate`, `mean_token_f1`, `judge_accuracy`, `mean_judge_score`) | dùng lại, không viết metrics mới |
| LLM `mock` | judge fallback heuristic tự động | chạy offline được, không cần key |
| `settings.freshness_threshold_days` | 180 | dùng biến, không hardcode |

## 1. Rủi ro đã phát hiện

- [ ] **Freshness sát ngưỡng:** snapshot có bài cũ nhất `2026-03-28` → tại 2026-09-25 đã 181 ngày (1/24 stale ≈ 4% < 25% → vẫn fresh). Càng về sau càng nhiều bài stale → ghi rõ `run_date` trong report.
- [ ] **Live API ≠ snapshot:** `source_filter` lấy 180 ngày gần nhất → dữ liệu live khác 24 bài mẫu. Mặc định **đọc snapshot**, chỉ gọi API khi `REFRESH_SOURCE=1`; lỗi mạng/429 → fallback snapshot.
- [ ] **Không ghi đè `crossref_response.json` bằng response lỗi/rỗng** (mất lineage).
- [ ] **Thứ tự signal CP1:** lệnh check GX đọc `clean_json` → phải chạy clean + ghi file trước.
- [x] `.gitignore` đã chặn `.env` ✅ (thêm `.fake/` cho output dữ liệu giả).
- [ ] Corruption phải **tất định** (seed / chọn theo index) → report lặp lại được.
- [ ] GX 1.x: chỉ dùng `gx.get_context(mode="ephemeral")` + `data_sources.add_pandas` (cú pháp cũ −10đ).

## 2. Checklist theo Checkpoint

### CP0 — Môi trường & Ingestion (Owner: M2)
- [ ] `.venv` Python 3.11 + `pip install -e .` → in `Môi trường sẵn sàng`
- [ ] `.env` từ `.env.example`, `LLM_PROVIDER=mock` để dev
- [ ] `parse_crossref_payload`: DOI, title[0], strip JATS + whitespace, author `given family`, subject, date-parts → `YYYY-MM-DD`, bỏ record thiếu DOI/title/abstract
- [ ] `fetch_source_records`: snapshot mặc định; live khi `REFRESH_SOURCE` (retry 429/503, backoff); ghi 2 raw file
- [ ] `load_raw_records`
- [ ] Signal: `Đã tải 24 bài báo`

### CP1 — Cleaning + GX + Freshness (Owner: M2 cleaning, M4 quality)
- [ ] `build_clean_dataframe`: normalize, dedupe `paper_id`, `age_days`, `authors_joined`, `categories_joined`, `summary_chars`, `text_for_embedding` (5 dòng Title/Authors/Published/Categories/Summary), sort
- [ ] Ghi `papers_clean.csv` + `.json`
- [ ] `run_data_quality_checks`: 4 expectation (RowCount 5–5000; NotNull `paper_id,title,text_for_embedding`; Unique `paper_id`; `summary` len ≥ 30) + nên thêm `title` len ≥ 8 để bắt truncate; ghi `data/quality/<report_name>_quality_report.json`
- [ ] `build_freshness_report`: latest/oldest, stale_rows, total_rows, stale_ratio, `is_fresh = ratio <= 0.25`
- [ ] Signal: `Clean thành công 24 dòng`, `Quality check status = True`

### CP2 — Test set + Index (Owner: M4 testset, M3 index)
- [ ] `build_test_set`: 10 câu, đủ 4 loại (vd 3/3/2/2), tất định, wording khớp bảng mục 0
- [ ] Không sinh lại nếu file đã có (trừ `REFRESH_TEST_SET`) → testset cố định cho cả 3 trạng thái
- [ ] Chroma `papers-baseline` 24 docs
- [ ] Signal: `Sinh được 10 câu hỏi test`

### CP3 — Baseline E2E (Owner: M1 pipeline, M4 report)
- [ ] `phase1.main`: raw → clean → GX → freshness → index → testset → evaluate → report (+ agent demo tuỳ chọn, bắt exception)
- [ ] `generate_phase1_report`: source summary, metrics, quality, freshness
- [ ] `python script/run_phase1.py` exit 0; có `baseline_metrics.json`, `phase1_report.md`, `baseline_quality_report.json`, `freshness_report.json`

### CP4 — Corruption (Owner: M2 corruption, M1 flow)
- [ ] 6 kịch bản: drop 20% mới nhất / blank summary / noise / title < 8 ký tự / published −365 ngày / duplicate rows; rebuild `text_for_embedding`, `age_days`
- [ ] `corruption_log.json` đủ 6 mục (loại, số dòng, paper_id bị ảnh hưởng)
- [ ] Index `papers-corrupted`, GX trên corrupted → **Fail** (`corrupted_quality_report.json`), freshness → stale
- [ ] `corrupted_metrics.json` giảm rõ so với baseline

### CP5 — Repair + So sánh (Owner: M1 flow, M4 report)
- [ ] Repair = `load_raw_records` → `build_clean_dataframe` → `papers_clean_repaired.*` → `papers-repaired` (chạy 2 lần kết quả y hệt)
- [ ] `repaired_metrics.json` ≈ baseline
- [ ] `generate_corruption_report`: bảng 3 cột Baseline/Corrupted/Repaired + GX/freshness + phân tích từng lỗi → metric nào tụt
- [ ] In bảng ra console; `python script/run_corruption_flow.py` exit 0

### CP6 — Nộp bài (Tất cả)
- [ ] `docs/TEAM.md`: tên nhóm, họ tên, MSSV, email, phần tự khai từng người
- [ ] `report/group_report.md` + `report/<MSSV>_HoTen.md` mỗi người; số liệu khớp JSON
- [ ] Không hardcode path tuyệt đối; không commit `.env`
- [ ] Commit artifact `data/` (raw, clean, eval, quality, results, reports, chroma)
- [ ] Chạy lại 2 script từ clone sạch
- [ ] 100% thành viên có commit trên `main` (Insights > Contributors)
- [ ] Mỗi người tự nộp link LMS
- [ ] Chuẩn bị demo 3–5 phút + Q&A: GX 1.x, Freshness SLA, embeddings, idempotent

### Bonus (sau khi ≥ 85đ)
- [ ] B2 Auto-repair: GX fail → tự trigger repair (rẻ nhất)
- [ ] B3 Pytest + script one-click
- [ ] B1 Dashboard (Streamlit/HTML)

## 3. Phân công & thứ tự phụ thuộc

| Thành viên | Vai trò | File |
|---|---|---|
| M1 | Pipeline Lead | `pipelines/phase1.py`, `pipelines/corruption_flow.py`, `.gitignore` |
| M2 | Data Foundation | `ingestion/crossref.py`, `ingestion/cleaning.py`, `ingestion/corruption.py` |
| M3 | RAG & Agent | kiểm `retrieval/` với mock + provider thật, verify 3 collection, agent demo |
| M4 | Observability & Eval | `observability/quality.py`, `evaluation/testset.py`, `observability/reporting.py` |

```text
crossref.py ──► cleaning.py ──┬─► quality.py ─────────┐
                              ├─► testset.py ──────────┼─► phase1.py ─► corruption_flow.py
                              └─► (index có sẵn) ──────┘        ▲
corruption.py (chỉ cần schema clean df) ────────────────────────┘
reporting.py (chỉ cần dict metrics/quality) ── làm song song từ đầu
```

Song song ngay từ phút 0: M2 `crossref`+`cleaning`, M4 `reporting`+`quality` (test bằng df giả), M1 khung `phase1`, M3 verify retrieval với mock.
