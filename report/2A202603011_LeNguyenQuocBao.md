# Bản nháp báo cáo cá nhân — Lê Nguyễn Quốc Bảo

> Bản này được tổng hợp từ commit và artifacts trong repository. **Quốc Bảo cần tự rà soát, chỉnh sửa theo trải nghiệm thực tế và xác nhận trước khi nộp**; người khác không thể tự khai thay thành viên.

## 1. Thông tin

| Mục | Nội dung |
| --- | --- |
| Họ và tên | Lê Nguyễn Quốc Bảo |
| MSSV | 2A202603011 |
| Nhóm | K4-L3A, Group 10 — Haki |
| Vai trò | Observability, Evaluation, Reporting & Pipeline Integration |
| Repository | https://github.com/TNTD-dev/K4-L3A-Day10-Group10-Haki |
| Git author | `Le Nguyen Quoc Bao <23520108@gm.uit.edu.vn>` |

## 2. Phần việc thể hiện trong repository

| Phần việc | Code/artifact | Kết quả kiểm chứng |
| --- | --- | --- |
| GX 1.x quality gate | `src/observability/quality.py`, `data/quality/*_quality_report.json` | Baseline/repaired critical pass; corrupted fail; freshness 0% → 31.82% → 0% stale |
| Bộ đề chuẩn | `src/evaluation/testset.py`, `data/eval/test_set.json` | 10 câu tất định, dùng chung ba trạng thái; 4 summary, 4 authors, 2 date |
| Metrics và báo cáo | `src/evaluation/metrics.py`, `src/observability/reporting.py`, `data/results/*_metrics.json` | Hit Rate 1.00 → 0.70 → 1.00; Token F1 1.00 → 0.3201 → 1.00 |
| Pipeline tích hợp | `src/pipelines/phase1.py`, `corruption_flow.py` | Hai entrypoint chạy end-to-end, tạo Phase 1/corruption reports |
| Hard benchmark | `src/evaluation/hard_benchmark.py`, `script/run_hard_benchmark.py` | 30 câu, có leakage filter và 6 câu không đáp án; `judge_fallbacks=0` |
| HTML report tĩnh | `src/observability/dashboard.py`, `data/reports/dashboard.html` | Bảng 3 trạng thái, quality/freshness và đồ thị |

Các commit chính: `0585b42`, `e280fa0`, `c3ad653`, `e0587c0`; chúng đã được merge vào `main`. Test observability trong `tests/test_observability.py` cùng test Data/RAG tạo tổng `23 passed` sau khi cập nhật dashboard.

## 3. Luồng kỹ thuật cần giải thích khi bảo vệ

1. `phase1.py` đọc raw/clean records, chạy GX/freshness **trước khi index**, rồi tạo baseline index, test set, metrics và báo cáo.
2. `corruption_flow.py` dùng cùng test set, tiêm sáu lỗi, chạy quality/freshness; corrupted index chỉ dùng để đo tác động. Khi gate fail, repair từ raw snapshot, validate và đánh giá lại.
3. GX 1.x dùng `gx.get_context(mode="ephemeral")`, Pandas datasource, dataframe asset và batch definition. Critical failures chặn production path; freshness và metadata coverage được ghi warning theo ngưỡng.
4. Hard benchmark tránh exact-title leakage, kiểm tra retrieval top-k và câu trả lời sinh từ context; thêm câu không đáp án để đo abstention/hallucination. Wilson CI nêu bất định của mẫu 24 câu có đáp án.

## 4. Kết quả và giới hạn cần nêu trung thực

| Metric | Baseline | Corrupted | Repaired |
| --- | ---: | ---: | ---: |
| Hit Rate (10 câu Guide) | 1.0000 | 0.7000 | 1.0000 |
| Token F1 | 1.0000 | 0.3201 | 1.0000 |
| Judge Accuracy | 1.0000 | 0.5000 | 1.0000 |
| GX critical | Pass | Fail | Pass |
| Stale ratio | 0% | 31.82% | 0% |

Hard benchmark đã commit có `hit@1` `0.792 → 0.542 → 0.792`, judge accuracy `0.958 → 0.542 → 0.958` và 0 judge fallback. Lượt chạy độc lập sau merge trên cùng đề giữ nguyên retrieval; judge accuracy Corrupted dao động thành `0.500`, phù hợp hạn chế LLM không hoàn toàn tất định.

Giới hạn quan trọng: raw live không có `subject` ở 24 records. Test set 10 câu vì thế **chưa phủ categories**; code dùng fallback sang loại có ground truth thay vì đưa đáp án rỗng. Quality report baseline/repaired có warning metadata `categories_joined` dù critical gate pass. Không nên báo cáo “đủ 4 loại câu hỏi” cho dataset hiện tại.

## 5. Phần Quốc Bảo cần tự hoàn tất

- [ ] Kiểm tra các mục trên có phản ánh đúng phần mình trực tiếp làm và mức hiểu của mình.
- [ ] Bổ sung một lỗi tích hợp mình trực tiếp xử lý, kèm cách tái hiện nếu muốn trình bày sâu hơn.
- [ ] Xác nhận báo cáo bằng tên/ngày của chính mình sau khi rà soát.
- [ ] Tự nộp link repository lên VLearn LMS và chuẩn bị giải thích GX, freshness, evaluation, hard benchmark khi demo.
