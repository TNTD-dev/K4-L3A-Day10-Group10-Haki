# Nhóm 10 — Haki: thành viên và phân công thực tế

**Lớp:** K4-L3A · **Repository:** [TNTD-dev/K4-L3A-Day10-Group10-Haki](https://github.com/TNTD-dev/K4-L3A-Day10-Group10-Haki) · **Nhánh nộp:** `main`

Nhóm thực tế có **hai thành viên**; mỗi người sở hữu nhiều checkpoint. Không sử dụng bảng 4 người của starter template. Thành viên nhóm đã xác nhận giảng viên/TA cho phép quy mô hai người, dù `report/README.md` mô tả mẫu chung 3–5 người.

## Thành viên

| Họ và tên | MSSV | Email trong Git commit | Phạm vi chính | Báo cáo cá nhân |
| --- | --- | --- | --- | --- |
| Trần Nguyễn Tiến Đức | 2A202602871 | `tntduc05@gmail.com` | CP0–CP2: config, Crossref live/raw, cleaning, embedding/Chroma; CP4–CP5: corruption, repair; bonus B1/B2: dashboard realtime và self-healing | [Báo cáo Đức](../report/2A202602871_TranNguyenTienDuc.md) |
| Lê Nguyễn Quốc Bảo | 2A202603011 | `23520108@gm.uit.edu.vn` | CP1: GX 1.x/freshness; CP2–CP5: test set, evaluation, reporting, hai pipeline tích hợp; hard benchmark | [Bản nháp báo cáo Bảo](../report/2A202603011_LeNguyenQuocBao.md) |

## Phân công theo checkpoint

| Checkpoint | Owner chính | Input → Output bàn giao | Bằng chứng |
| --- | --- | --- | --- |
| CP0 — Ingestion | Đức | Crossref API → raw response và 24 `PaperRecord` | `src/ingestion/crossref.py`, `data/raw/` |
| CP1 — Cleaning & GX | Đức (cleaning), Bảo (GX) | Raw → clean 16 cột; GX/freshness reports | `src/ingestion/cleaning.py`, `src/observability/quality.py`, `data/quality/` |
| CP2 — Test set & index | Bảo (test set), Đức (index) | Clean → fixed questions; `papers-baseline` 24 docs | `data/eval/test_set.json`, `data/embeddings/papers_embeddings.json` |
| CP3 — Baseline | Bảo (orchestration/report), Đức (Data/RAG contract) | Clean + GX + index + eval → Phase 1 report | `src/pipelines/phase1.py`, `data/reports/phase1_report.md` |
| CP4 — Corruption | Đức (fault injection), Bảo (impact measurement) | Six scenarios → corrupted 22 rows, metrics | `src/ingestion/corruption.py`, `data/results/corruption_log.json` |
| CP5 — Repair & comparison | Đức (raw repair/index), Bảo (flow/report) | Raw → repaired 24 rows; comparison report | `src/pipelines/corruption_flow.py`, `data/reports/corruption_report.md` |
| Bonus B1/B2 | Đức (dashboard/self-healing), Bảo (GX/metrics artifacts integrated into UI) | Quality/metrics → realtime UI; failure drill → validated repaired index | `dashboard/`, `src/automation/`, `data/reports/bonus_dashboard_success.png` |

## Cá nhân

### Trần Nguyễn Tiến Đức — 2A202602871

- Sở hữu `src/core/config.py`, `src/ingestion/crossref.py`, `cleaning.py`, phần corruption/repair, `src/retrieval/`, `src/automation/` và dashboard FastAPI/HTML.
- Thu Crossref live và giữ raw lineage; build ba Chroma collection bằng OpenAI `text-embedding-3-small`; self-healing tự detect, quarantine, revalidate và publish/rollback an toàn.
- Bàn giao DOI ổn định, clean schema 16 cột, các index/manifests và failure-drill events để Observability/Evaluation dùng lại.
- Bằng chứng chi tiết và giới hạn được ghi trong [báo cáo cá nhân](../report/2A202602871_TranNguyenTienDuc.md); commit Data/RAG và bonus có author `Duc Tran`.

### Lê Nguyễn Quốc Bảo — 2A202603011

- Sở hữu `src/observability/quality.py`, `reporting.py`, `src/evaluation/testset.py`, `hard_benchmark.py` và hai pipeline `phase1.py`, `corruption_flow.py`.
- Xây GX 1.x ephemeral gate và Freshness SLA; sinh fixed test set; đo Hit Rate, Token F1, LLM judge; lập báo cáo Baseline/Corrupted/Repaired và hard benchmark 30 câu.
- Tích hợp Data/RAG contract với quality gate, evaluation và reporting; commit trên `main` có author `Le Nguyen Quoc Bao`.
- [Bản nháp báo cáo cá nhân](../report/2A202603011_LeNguyenQuocBao.md) tổng hợp từ commit/artifact; Quốc Bảo cần rà soát và xác nhận bằng chính mình trước khi nộp.

## Ghi chú trung thực về nghiệm thu

- Raw live hiện không có `subject`: test set 10 câu chỉ gồm summary/authors/date, chưa có categories theo Guide. Không tạo ground truth rỗng hoặc nhãn giả.
- Embedding artifacts dùng OpenAI; rubric gốc ghi MiniLM. Nhóm sẽ giải thích lựa chọn này khi bảo vệ.
- `group_report.md` và hai báo cáo cá nhân mô tả số liệu từ artifacts thật. Mỗi thành viên phải tự nộp link repo trên LMS và tự trình bày phần việc của mình.
