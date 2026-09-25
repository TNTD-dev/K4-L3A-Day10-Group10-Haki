# Kịch bản demo 3 phút — Group 10 Haki

> Dùng để trình bày trực tiếp; không thay thế việc mỗi thành viên tự nộp LMS hoặc tự giải thích code.

## Chuẩn bị trước khi lên bảng

1. Kiểm tra `.env` có `OPENAI_API_KEY`, `EMBEDDING_PROVIDER=openai`, `EMBEDDING_MODEL=text-embedding-3-small`. Đặt `REFRESH_SOURCE=false` để demo trên đúng snapshot live đã lưu; không mở `.env` trước lớp.
2. Chạy `uv run python dashboard/app.py --repair-source snapshot`, mở `http://127.0.0.1:8000`.
3. Mở sẵn `data/reports/corruption_report.md` và `data/reports/hard_benchmark_report.md`. Nếu mạng/API chậm, trình chiếu [ảnh nghiệm thu](../data/reports/bonus_dashboard_success.png) và artifacts đã commit, không bịa kết quả live.

## Lời trình bày gợi ý

**0:00–0:40 — Bài toán và lineage.** “Nhóm lấy 24 paper từ Crossref live và giữ cả raw response lẫn parsed records. Cleaning tạo DOI làm khóa ổn định, `age_days` và văn bản nhúng năm phần. OpenAI embedding nạp ba Chroma collections riêng.” Trỏ vào KPI Active Index và phần Dataset comparison.

**0:40–1:20 — Quality và đánh giá chuẩn.** “GX 1.x kiểm row count, null, DOI unique, độ dài summary; Freshness SLA báo lỗi khi trên 25% paper quá 180 ngày. Cùng 10 câu dùng cho cả ba trạng thái. Hit Rate là `1.00 → 0.70 → 1.00`, Token F1 là `1.00 → 0.3201 → 1.00`.” Trỏ vào bảng `Passed / Failed / Passed` và biểu đồ tuổi bài.

**1:20–2:15 — Tiêm lỗi và tự phục hồi.** Bấm **Run failure drill** một lần. “Sáu lỗi gồm mất bài mới, blank summary, noise, short title, stale date và DOI trùng. UI nhận event qua SSE, phát hiện lỗi, cách ly index bẩn, dựng lại từ raw, kiểm định rồi mới publish `papers-repaired`. Không có bước repair thủ công.” Trỏ vào state flow, audit timeline và affected paper IDs. Chờ `HEALTHY` và 24 repaired documents; nếu chưa xong, trình bày tiếp trên artifact có sẵn thay vì bấm lặp.

**2:15–3:00 — Hard benchmark và giới hạn.** “Bộ 10 câu có exact title nên baseline dễ đạt 1.0. Hard benchmark 30 câu không chứa exact title: hit@1 `0.792 → 0.542 → 0.792`, judge accuracy artifact `0.958 → 0.542 → 0.958`, judge fallback 0. Bộ raw hiện không có Crossref `subject`, vì vậy test set 10 câu chưa có categories; đây là giới hạn công khai, không tạo nhãn giả.”

## Q&A ngắn

- **Vì sao corrupted vẫn được index khi GX fail?** Chỉ trong nhánh thí nghiệm để đo silent failure; production/repaired path phải qua gate trước khi publish.
- **Repair có idempotent không?** Có: không vá dataframe bẩn, đọc lại raw và chạy cùng cleaning; fingerprint repaired khớp baseline.
- **Vì sao OpenAI thay MiniLM?** Nhóm dùng factory chung cho indexing/query, manifest ghi provider/model để tránh mismatch; MiniLM vẫn cấu hình được. Artifacts nộp dùng OpenAI, cần nêu rõ khác tên model trong rubric.
- **B1/B2 chứng minh bằng gì?** Giao diện có biểu đồ, KPI, trạng thái và SSE; B2 có event JSONL, pre/post quality reports, staging index và rollback khi publish thất bại.
- **Điểm còn thiếu?** `categories` trên bộ 10 câu vì raw live thiếu `subject`. Thành viên nhóm đã xác nhận giảng viên/TA cho phép nhóm hai người.
