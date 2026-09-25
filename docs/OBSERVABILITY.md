# Observability Workstream — Checklist, Contract & Demo

> Đây là ghi chú thiết kế của workstream. Trạng thái và số liệu nộp bài cuối cùng nằm trong [`report/group_report.md`](../report/group_report.md). Corpus live hiện không có `subject`, nên test set thực tế là 4 summary / 4 authors / 2 date, **không** có categories.

> Owner: **Observability & Evaluation**. Rubric phụ trách: #6 (10đ) + #7 (15đ) + #8 (15đ) + Bonus B1/B2.
> Nguyên tắc: **không sửa** `ingestion/crossref.py`, `ingestion/cleaning.py`, `retrieval/*`, `pipelines/phase1.py` cho tới khi merge.

## 1. Checklist

### A. Code
- [x] `observability/quality.py` — GX 1.x ephemeral (`get_context` → `add_pandas` → `add_dataframe_asset` → `add_batch_definition_whole_dataframe`)
  - [x] 4 expectation bắt buộc: RowCount 5–5000, NotNull (`paper_id,title,text_for_embedding`), Unique `paper_id`, `summary` len ≥ 30
  - [x] Thêm: schema contract, volume vs baseline, `title` len ≥ 8, freshness `age_days` (warning), `data_fingerprint`, `enforce_quality_gate`
  - [x] Không raise khi data xấu → `success=False`; ghi `data/quality/<name>_quality_report.json`
- [x] `build_freshness_report` — stale_ratio, `is_fresh` (≤ 25%), `latest_age_days`, phân bố `age_days`
- [x] `evaluation/testset.py` — 10 câu tất định, wording khớp `qa.py`, rải đều theo ngày; thực tế 4/4/2 và thiếu categories do raw source
- [x] `ingestion/corruption.py` — 6 lỗi tất định, không sửa input, rebuild `text_for_embedding`, log đủ 6 mục
- [x] `pipelines/corruption_flow.py` — corrupt → gate → eval → **auto-repair (B2)** → gate → eval → report → dashboard
- [x] `observability/reporting.py` — phase1 report + bảng 3 trạng thái + detection matrix + verdict
- [x] `observability/dashboard.py` — HTML tĩnh, không thêm dependency (**B1**)
- [x] `tests/test_observability.py` — 15 test trên raw snapshot thật, pass (một phần B3)

### B. Kiểm chứng (sau khi cài env)
- [x] `uv run --extra dev pytest -q` → 23 passed sau cập nhật dashboard
- [x] Soát `corruption_report.md`: đủ 3 cột, silent failure = `inject_noise`

### C. Merge với RAG
- [x] Clean dataframe có đủ `REQUIRED_COLUMNS` và index metadata contract.
- [x] `phase1.py` chạy quality gate trước khi build baseline index; xuất freshness, test set, metrics và report.
- [x] Chạy `run_phase1.py` → `run_corruption_flow.py` trên raw 24 records; CP1 → CP5 hoàn thành về kỹ thuật.
- [x] Corrupted metrics giảm và repaired khớp baseline; số liệu giữ nguyên từ pipeline.
- [x] Artifacts thật trong `data/` đã được commit vào `main`.

## 1b. Kiến trúc — 5 pillar của Data Observability

| Pillar | Kiểm tra | Severity | Bắt lỗi tiêm |
|---|---|---|---|
| Schema | `ExpectColumnToExist` × 8 cột contract | critical | lệch contract khi merge |
| Volume | `ExpectTableRowCountToBeBetween(5–5000)`; `ExpectColumnUniqueValueCountToBeBetween(paper_id ≥ 90% baseline)` | critical | drop_latest_records |
| Completeness / Uniqueness | `NotNull(paper_id,title,text_for_embedding)`, `Unique(paper_id)` | critical | duplicate_rows |
| Distribution | length `summary ≥ 30`, `title ≥ 8` | critical | blank_summary, truncate_title |
| Freshness | `age_days ≤ 180 mostly 0.75` + `freshness_report.json` | **warning** (Guide: "gắn cờ cảnh báo") | stale_date |
| Lineage | raw snapshot bất biến + `data_fingerprint` (sha256 nội dung, bỏ `age_days`, không phụ thuộc thứ tự) | — | chứng minh repair idempotent |

- **Gate:** `report["success"]` = mọi expectation critical pass. `enforce_quality_gate()` raise `DataQualityError` và được gọi **trước khi index** ở production path (repair; phase1 phía RAG cũng nên gọi).
- **Audit mode:** nhánh corrupted cố ý không enforce, vẫn index để *đo* silent failure. Đây là thí nghiệm, không phải production.
- **Kết quả chạy thật:** GX/freshness phát hiện volume, blank summary, truncate title, stale date và duplicate; `inject_noise` giữ độ dài hợp lệ nên là silent đối với GX, nhưng core self-healing gate B2 phát hiện marker `~#`. Token F1 cũng cho thấy tác động tổng hợp.

## 2. Merge contract (RAG ↔ Observability)

| Hạng mục | Quy ước |
|---|---|
| Clean df columns | `paper_id, title, summary, authors_joined, categories_joined, published, age_days, text_for_embedding` (+ `abs_url, pdf_url` cho index) |
| `published` | string `YYYY-MM-DD` |
| `age_days` | int, `(run_date - published).days` |
| `text_for_embedding` | `ingestion.cleaning.build_embedding_text` tạo 5 dòng Title/Authors/Published/Categories/Summary; corruption gọi lại hàm này để không lệch |
| Repair | `corruption_flow.repair_from_raw` gọi `load_raw_records(raw_records_json)` → `build_clean_dataframe(records, now)` |
| Evaluate | `corruption_flow.evaluate` gọi `LocalEmbeddingIndex.build(df, s, <embeddings path>)` + `evaluate_pipeline` |

Thiếu cột → GX báo `expect_column_to_exist(<col>)` FAIL chứ không crash: lỗi hợp đồng lộ ngay khi merge.

## 2b. Kỳ vọng số liệu thật & cơ chế tạo tác động

Với `qa.py` hiện tại (exact title lookup + trích trường metadata), dự đoán **trước khi chạy** (phải xác nhận bằng số thật):

| Câu hỏi bị đánh | Lỗi | Cơ chế làm sai |
|---|---|---|
| 3 bài mới nhất trong test set | drop_latest | paper biến mất → retrieval miss, trả lời từ paper khác |
| summary | blank_summary | câu trả lời rỗng → F1 = 0 |
| summary | inject_noise | `Ret~#rie~#val` → token sai → F1 ≈ 0, GX vẫn PASS (**silent**) |
| authors / categories | truncate_title | exact lookup hỏng → phụ thuộc vector search |
| date | stale_date | trả ngày lệch 365 ngày → F1 = 0 |

- **Baseline** gần trần (hit ≈ 1.0, F1 ≈ 1.0) vì test set sinh từ chính dữ liệu sạch.
- **Repaired** dựng lại từ raw nên **bằng** baseline. Đây là tiêu chí thành công (idempotent). **Không thể và không được** làm repaired "cao hơn" bằng cách chỉnh số (bịa số −20đ). Cách hợp lệ duy nhất để repaired > baseline là repair *thực sự làm sạch tốt hơn* một lỗi có sẵn trong baseline, và phải giải thích được trong report.
- `sanity_check` trong flow chỉ **cảnh báo** khi corrupted không giảm hoặc repaired lệch, không bao giờ sửa số.

## 3. Đề xuất demo (3–5 phút)

Câu chuyện: *"Agent không báo lỗi — nhưng dữ liệu thì có"*.

| Phút | Màn hình | Lời thoại chính |
|---|---|---|
| 0:00 | Dashboard, dải trạng thái Baseline | "Pipeline sạch: GX PASS, Freshness FRESH, hit rate X." |
| 0:40 | Terminal: `python script/run_corruption_flow.py` | Log `[gate] corrupted quality=False failed=[...]` hiện ngay |
| 1:30 | Dashboard → cột **Corrupted** đỏ, bảng Corruption → Detection | "GX/freshness bắt volume, blank, title, stale và duplicate; noise lọt qua GX nhưng core B2 bắt marker và Token F1 giảm." |
| 2:30 | Mở `corrupted_answers.json`, 1 câu trả lời sai tự tin | "Đây là silent failure: câu trả lời trôi chảy nhưng sai." |
| 3:15 | Dashboard → cột **Repaired** xanh, banner "đã phục hồi" | "Gate fail → auto-repair từ raw bất biến. Chạy lại lần 2 ra y hệt → idempotent." |
| 4:00 | Q&A | xem mục 5 |

Mẹo sân khấu: chạy trước 1 lần để có sẵn artifact; khi demo chạy lại live (khoảng 1–2 phút) và nói trong lúc chờ.

## 4. UI/UX dashboard (`data/reports/dashboard.html`)

**Mục tiêu:** trong 5 giây người xem trả lời được: *dữ liệu có đang ổn không? lỗi nào bị bắt? đã hồi phục chưa?*

Bố cục từ trên xuống (theo thứ tự câu hỏi người xem sẽ đặt):
1. **Banner kết luận**: xanh "đã phục hồi" / đỏ "chưa khớp".
2. **Dải trạng thái 3 bước** Baseline → Corrupted → Repaired: mỗi ô có pill GX PASS/FAIL và FRESH/STALE, viền màu theo trạng thái.
3. **KPI cards** (Hit Rate, F1, Judge Acc, Judge Score): 3 giá trị cạnh nhau, delta đỏ khi giảm.
4. **Grouped bar chart** (SVG inline): so sánh 3 trạng thái trên cùng thang 0–1.
5. **Ma trận GX**: hàng là expectation, cột là trạng thái, ô ✓/✗ kèm số dòng lỗi. Nhìn cột Corrupted là biết lỗi bị bắt ở đâu.
6. **Freshness**: thanh gauge stale ratio có vạch SLA 25%, histogram `age_days` (cột quá 180 ngày tô đỏ) và `latest_age_days`.
7. **Bảng Corruption → Detection**: badge DETECTED/SILENT, luận điểm chính của bài.

Nguyên tắc thiết kế: mã màu cố định (xanh dương = baseline, đỏ = corrupted, xanh lá = repaired) dùng xuyên suốt mọi thành phần; hỗ trợ dark mode; responsive tới 375px; không phụ thuộc CDN, mở offline được; tooltip trên bar chart và histogram.

Mở rộng nếu còn thời gian: ghi lịch sử mỗi lần chạy thành `data/quality/history.jsonl` và vẽ drift theo thời gian.

## 5. Chuẩn bị Q&A

- **Vì sao ephemeral context?** Chạy trên RAM, không sinh file cấu hình GX; phù hợp gate trong pipeline/CI.
- **Vì sao GX PASS mà RAG vẫn sai?** GX kiểm cấu trúc (null, unique, length), còn noise giữ nguyên độ dài nên vẫn qua. Cần lớp thứ hai là metric RAG.
- **Drop 20% bài mới sao không bị bắt?** stale_ratio không tăng khi mất bài mới; phải theo dõi `latest_age_days` hoặc số dòng so với lần chạy trước.
- **Idempotent là gì ở đây?** Repair luôn dựng lại từ raw snapshot bất biến, không vá dữ liệu hỏng, nên chạy N lần đều ra cùng output.
- **Vì sao test set cố định?** Để 3 trạng thái thi cùng một đề; nếu đổi đề thì so sánh vô nghĩa.
