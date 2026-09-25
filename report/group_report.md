# Báo cáo nhóm — Day 10 Data Pipeline & Data Observability

## 1. Thông tin bài nộp

| Mục | Thông tin |
| --- | --- |
| Lớp/nhóm | K4-L3A, Group 10 — Haki |
| Repository | https://github.com/TNTD-dev/K4-L3A-Day10-Group10-Haki |
| Ngày hoàn thành kỹ thuật | 2026-09-25 |
| Nhánh nộp | `main` |

| Thành viên | MSSV | Phần việc chính | Bằng chứng |
| --- | --- | --- | --- |
| Trần Nguyễn Tiến Đức | 2A202602871 | Crossref ingestion, cleaning, OpenAI embedding/Chroma, corruption/repair, dashboard realtime và self-healing | `src/ingestion/`, `src/retrieval/`, `src/automation/`, `dashboard/`, [báo cáo cá nhân](2A202602871_TranNguyenTienDuc.md) |
| Lê Nguyễn Quốc Bảo | 2A202603011 | GX 1.x, freshness, test set, metrics/hard benchmark, reporting và hai pipeline tích hợp | `src/observability/`, `src/evaluation/`, `src/pipelines/`, [bản nháp báo cáo cá nhân](2A202603011_LeNguyenQuocBao.md) |

Nhóm thực tế có hai thành viên. Thành viên nhóm đã xác nhận giảng viên/TA cho phép quy mô này; mẫu `report/README.md` mô tả 3–5 người không được dùng để khai thêm người không tham gia.

## 2. Tóm tắt kết quả

Nhóm đã xây dựng pipeline đi từ Crossref live đến raw lineage, clean dataframe, ba collection ChromaDB và đánh giá RAG. Lần chạy được lưu trong repo có 24 bài báo hợp lệ; raw response và bản `PaperRecord` được giữ riêng để có thể tái tạo dữ liệu. Dữ liệu sạch có `paper_id` duy nhất, `age_days` và văn bản nhúng đủ năm phần. Quality gate dùng Great Expectations 1.x cùng Freshness SLA 180 ngày/25%. Nhóm tiêm sáu kịch bản lỗi có log affected DOI, tạo bộ corrupted 22 dòng và dựng lại bộ repaired 24 dòng từ raw, không vá tay dữ liệu lỗi. Trên cùng bộ 10 câu, Hit Rate thay đổi `1.00 → 0.70 → 1.00` và Token F1 `1.00 → 0.3201 → 1.00`. Hard benchmark 30 câu bổ sung kiểm tra truy hồi không có exact title và khả năng từ chối câu không có đáp án. Dashboard HTML hiển thị chất lượng, độ tươi, metrics và sự kiện realtime; failure drill tự phát hiện, cách ly, repair, kiểm định và publish index. Giới hạn lớn nhất là Crossref không cung cấp `subject` cho 24 bài hiện tại, nên test set bắt buộc chưa có câu `categories`.

## 3. Kiến trúc và data contract

```text
Crossref live / raw snapshot
  → crossref_response.json → crossref_records.json
  → cleaning → papers_clean.csv/json
  → GX + freshness → OpenAI embedding → papers-baseline
  → fixed test set + evaluation
  → six corruptions → papers-corrupted → evaluation
  → rebuild từ raw → GX → papers-repaired → evaluation/report
                                  ↘ dashboard SSE + self-healing audit
```

`paper_id` là DOI, giữ nguyên qua raw, clean, Chroma metadata và `ground_truth_doc_ids`. Clean dataframe có 16 cột: `paper_id`, `title`, `summary`, `authors`, `categories`, `primary_category`, `published`, `updated`, `age_days`, `authors_joined`, `categories_joined`, `summary_chars`, `text_for_embedding`, `abs_url`, `pdf_url`, `comment`. `text_for_embedding` ghép Title, Authors, Published, Categories và Summary. Đầu vào thiếu DOI/title/abstract bị bỏ; DOI trùng được giữ bản đầu tiên. Ngày chuẩn hóa ISO và `age_days` tính từ ngày chạy.

## 4. Cấu hình và cách tái hiện

| Cấu hình | Giá trị của artifacts đã nộp |
| --- | --- |
| Nguồn | Crossref REST API, query `agentic retrieval augmented generation large language model`, 24 records |
| Embedding | `openai/text-embedding-3-small`, cosine ChromaDB |
| LLM báo cáo đã nộp | `openai/gpt-5.6-luna` |
| Retrieval | `top_k=4` |
| Freshness SLA | `age_days > 180`, cảnh báo khi stale ratio `>25%` |
| Tập kiểm tra chuẩn | `data/eval/test_set.json`, cố định cho ba trạng thái |
| Tập hard benchmark | `data/eval/hard_test_set.json`, 30 câu, fingerprint `edc38d2b51eeb905` |

Không lưu `.env` hoặc API key trong Git. Cài dependencies bằng `uv sync --extra dev`, tạo `.env` từ `.env.example`, rồi chạy:

```bash
uv run python script/run_phase1.py
uv run python script/run_corruption_flow.py
uv run python script/run_hard_benchmark.py
uv run python dashboard/app.py
```

`REFRESH_SOURCE=true` lấy Crossref live; `false` tái hiện từ raw snapshot đã lưu. `run_hard_benchmark.py` mặc định dùng bộ đề đóng băng; `--rebuild` sinh lại đề bằng LLM và làm thay đổi cơ sở so sánh. Hai pipeline bắt buộc và self-healing đã được chạy lại trên checkout tạm sau khi merge; 23 pytest pass. Hard benchmark cũng được chạy lại độc lập trên cùng fingerprint, không ghi đè artifact đã nộp vì LLM judge có dao động giữa các lượt.

## 5. Raw ingestion, cleaning và quality gate

Raw artifacts: `data/raw/crossref_response.json` là payload API; `data/raw/crossref_records.json` là 24 `PaperRecord` đã parse. Parser loại JATS/XML, giải mã HTML entities, chuẩn hóa whitespace và ngày, nhận PDF URL nếu Crossref có. Request có retry/backoff cho HTTP 429 và 5xx, fallback snapshot khi API lỗi.

GX 1.x dùng ephemeral context, dataframe asset và batch definition; baseline có 18 expectations. Các hàng rào bắt buộc gồm row count 5–5000, not-null `paper_id/title/text_for_embedding`, unique `paper_id`, summary dài tối thiểu 30 ký tự. Nhóm bổ sung kiểm tra schema, title dài tối thiểu 8 ký tự, volume so với baseline và freshness. `success=true` nghĩa là mọi check critical pass; `gx_suite_success=false` ở baseline/repaired vì **warning** `categories_joined` rỗng trên cả 24 record, không phải vì dữ liệu critical hỏng.

| Trạng thái | GX critical | Stale rows / total | Stale ratio | Freshness |
| --- | --- | ---: | ---: | --- |
| Baseline | Pass | 0/24 | 0% | Fresh |
| Corrupted | Fail: volume, DOI trùng, title/summary ngắn | 7/22 | 31.82% | Stale |
| Repaired | Pass | 0/24 | 0% | Fresh |

Bằng chứng: `data/quality/baseline_quality_report.json`, `corrupted_quality_report.json`, `repaired_quality_report.json` và các freshness reports.

## 6. Test set và đánh giá

Bộ chuẩn 10 câu có 4 `summary`, 4 `authors`, 2 `date`; **chưa có `categories`** vì toàn bộ `categories_joined` của raw live rỗng. Builder không tạo ground truth rỗng mà thay bằng loại có đáp án, đồng thời in cảnh báo. Điều này giữ metric trung thực nhưng chưa đáp ứng đủ 4 dạng của Guide. Cùng file test set được dùng cho cả ba collection; ground truth DOI không đổi.

| Metric | Baseline | Corrupted | Repaired |
| --- | ---: | ---: | ---: |
| Retrieval Hit Rate | 1.0000 | 0.7000 | 1.0000 |
| Mean Token F1 | 1.0000 | 0.3201 | 1.0000 |
| LLM Judge Accuracy | 1.0000 | 0.5000 | 1.0000 |
| Mean Judge Score (1–5) | 5.0000 | 3.0000 | 5.0000 |

Bằng chứng: `data/results/*_metrics.json`, `data/results/*_answers.json`, `data/reports/phase1_report.md` và `data/reports/corruption_report.md`. Ragas mặc định tắt (`RUN_RAGAS` không bật), nên nhóm không công bố điểm Ragas.

Hard benchmark dùng 24 câu có đáp án và 6 câu không có đáp án, không đưa exact title vào prompt truy hồi. Artifact đã nộp ghi `hit@1` `0.792 → 0.542 → 0.792`, judge accuracy `0.958 → 0.542 → 0.958`, hallucination rate trên 6 câu không đáp án bằng 0 và `judge_fallbacks=0`. Lượt chạy độc lập sau merge cho cùng retrieval nhưng judge accuracy Corrupted là `0.500`; đây là dao động LLM, không sửa số cũ. Khoảng tin cậy Wilson và hạn chế cỡ mẫu được ghi trong `data/reports/hard_benchmark_report.md`.

## 7. Corruption, repair và phân tích tác động

| Lỗi | Số DOI tác động | Dấu hiệu |
| --- | ---: | --- |
| Drop latest 20% | 5 | Mất 5 bài mới; volume so baseline fail |
| Blank summary | 2 | Summary length fail |
| Inject noise `~#` | 2 | Token F1 giảm; core self-healing gate nhận marker |
| Truncate title | 2 | Title length fail, exact lookup bị ảnh hưởng |
| Stale date −365 ngày | 7 | Freshness ratio lên 31.82% |
| Duplicate rows | 3 | DOI uniqueness fail |

`data/results/corruption_log.json` ghi affected DOI, tham số và row count. Corrupted còn 22 dòng (`24 − 5 + 3`). Quality gate phát hiện lỗi critical; corrupted index chỉ được dựng trong chế độ thí nghiệm để đo silent failure, không dùng làm serving index. Repair đọc lại `crossref_records.json`, chạy lại cleaning, kiểm định và rebuild collection `papers-repaired`. Fingerprint dữ liệu repaired khớp baseline (`cac0b8b1a956cb10`), Hit Rate/F1 phục hồi về baseline.

B2 self-healing riêng dùng core gate + GX adapter, ghi event JSONL, cách ly corrupted data, build repaired index ở staging và chỉ promote sau post-repair pass. Lượt drill tích hợp sau merge kết thúc `HEALTHY`, 24 repaired documents; khi publish lỗi, collection cũ được rollback. B1 dashboard HTML Light Enterprise dùng FastAPI, SSE, biểu đồ tuổi bài và drift, bảng quality/metrics ba trạng thái, timeline và affected DOI. Ảnh nghiệm thu tại `data/reports/bonus_dashboard_success.png`.

## 8. Vấn đề tích hợp và giới hạn

1. Hai nhánh cùng chỉnh `src/retrieval/index.py`; lúc merge đã giữ cả hỗ trợ staging collection và fix persist path ngoài project. Test suite sau cập nhật dashboard: `23 passed`.
2. Corruption đổi marker sang `~#`; core self-healing gate ban đầu còn tìm marker cũ. Đã dùng chung hằng số `NOISE_MARKER` và thêm test xác nhận gate phát hiện đúng.
3. Crossref live không trả `subject` cho corpus hiện tại: GX báo warning và test set thiếu `categories`. Không tự bịa nhãn chủ đề; muốn đạt đúng Guide cần lấy thêm paper có `subject` thật rồi build lại toàn bộ artifacts/benchmark.
4. Rubric ghi cụ thể MiniLM; nhóm dùng OpenAI embedding theo lựa chọn kỹ thuật. Factory vẫn hỗ trợ MiniLM, nhưng artifacts nộp là OpenAI. Cần giải thích trade-off khi bảo vệ.
5. Baseline 10 câu đặt exact title trong câu hỏi nên điểm 1.0 có thiên lệch dễ; hard benchmark 30 câu được thêm để đo realistic retrieval. Hard benchmark vẫn nhỏ và LLM sinh/chấm cùng model, nên không diễn giải chênh lệch một câu là cải thiện chắc chắn.

## 9. Checklist nộp bài

- [x] Hai pipeline bắt buộc chạy end-to-end; artifacts raw/clean/index/quality/metrics/reports tồn tại.
- [x] Cùng test set cho Baseline, Corrupted và Repaired; số liệu trong bảng lấy từ JSON đã commit.
- [x] Dashboard và self-healing chạy; ảnh UI được lưu; 23 tests pass.
- [x] Hai thành viên có commit trên `main`; không có `.env` trong Git history đã kiểm tra.
- [ ] Quốc Bảo rà soát và xác nhận bản nháp báo cáo cá nhân của mình.
- [ ] Hai thành viên tự nộp link repo lên VLearn LMS; demo/Q&A trước lớp là việc thực hiện trực tiếp.
- [x] Thành viên nhóm xác nhận giảng viên/TA cho phép nhóm hai người.

Các mục chưa tick là hành động của thành viên/giảng viên, không được ghi là đã hoàn tất thay họ.
