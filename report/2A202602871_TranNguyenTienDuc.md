# Báo cáo vai trò cá nhân — Day 10: Data Pipeline & Data Observability

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | Trần Nguyễn Tiến Đức |
| MSSV | 2A202602871 |
| Khóa/Lớp | K4 |
| Tên nhóm | Group 10 — Haki |
| Vai trò chính | Data Foundation, Corruption/Repair & RAG Owner |
| Repository | `https://github.com/TNTD-dev/K4-L3A-Day10-Group10-Haki` |
| Ngày hoàn thành | 2026-09-25 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| Cấu hình embedding | `src/core/config.py`, `src/retrieval/embeddings.py` | Biến môi trường | OpenAI embedding client cấu hình được | Hoàn thành |
| Raw ingestion | `src/ingestion/crossref.py` | Crossref REST API hoặc snapshot | Raw response và 24 parsed records | Hoàn thành |
| Cleaning và data model | `src/ingestion/cleaning.py` | `list[PaperRecord]` | Clean CSV/JSON 24 dòng | Hoàn thành |
| Vector index | `src/retrieval/index.py` | Clean dataframe | Ba ChromaDB collections và embedding manifests | Hoàn thành |
| Retrieval và QA | `src/retrieval/qa.py`, `src/retrieval/agent.py` | Câu hỏi và vector index | Retrieved documents và câu trả lời theo corpus | Hoàn thành |
| Synthetic corruption | `src/ingestion/corruption.py` | Baseline clean dataframe | Corrupted dataset 22 dòng và corruption log | Hoàn thành |
| Idempotent repair | `rebuild_clean_dataframe_from_raw()` | Raw records và run date | Repaired dataset/index 24 dòng | Hoàn thành |
| Kiểm thử Data/RAG | `tests/test_data_rag.py` | Fixtures và artifacts | 6 test cases cho contract chính | Hoàn thành |
| Bonus B1: Dashboard realtime | `dashboard/` | Metrics, GX, datasets và audit events | HTML Light Enterprise, SSE và ảnh nghiệm thu | Hoàn thành |
| Bonus B2: Self-healing | `src/automation/` | Failure drill, raw lineage, quality gate | Quarantine, repair, staging publish/rollback | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Định nghĩa clean-data contract | Observability và Evaluation | Cố định 16 cột để GX, test-set và metrics có thể tích hợp ổn định |
| Cô lập ba trạng thái dữ liệu | Evaluation pipeline | Cung cấp ba collection riêng cho baseline, corrupted và repaired |
| Ghi manifest có provider/model | Pipeline integration | Cho phép phát hiện index được tạo bằng embedding model không tương thích |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Lấy dữ liệu Crossref live | `fetch_source_records()` | 24 records; raw response được giữ nguyên | Đọc `data/raw/crossref_response.json` và `crossref_records.json` |
| Chuẩn hóa dữ liệu | `build_clean_dataframe()` | 24 dòng, `paper_id` duy nhất, đủ `text_for_embedding` | Đọc `data/clean/papers_clean.json` |
| Build baseline index | `LocalEmbeddingIndex.build()` | `papers-baseline`: 24 documents | Kiểm tra Chroma collection count |
| Tiêm sáu dạng lỗi | `corrupt_clean_dataframe()` | 22 dòng sau corruption và log chi tiết affected IDs | Đọc `data/results/corruption_log.json` |
| Build corrupted index | `LocalEmbeddingIndex.build()` | `papers-corrupted`: 22 documents | Kiểm tra Chroma collection count |
| Repair từ raw | `rebuild_clean_dataframe_from_raw()` | 24 dòng repaired, khớp baseline | So sánh clean và repaired JSON |
| Build repaired index | `LocalEmbeddingIndex.build()` | `papers-repaired`: 24 documents | Kiểm tra Chroma collection count |
| Semantic retrieval | `LocalEmbeddingIndex.search()` | Trả về top-k paper IDs, nội dung và metadata | Smoke query `agentic retrieval augmented generation` |

Output cụ thể của phần việc là chuỗi artifacts có lineage đầy đủ:

```text
Crossref live response (24 items)
  -> parsed raw records (24)
  -> baseline clean dataset (24)
  -> corrupted dataset (22)
  -> repaired dataset (24, khớp baseline)
  -> Chroma collections: baseline 24, corrupted 22, repaired 24
```

Các commit chính: `b118b7a`, `e7dc37f`, `f53a997`, `84a2071`, `3ba073e`, `0a4b81f`, `4413c69` và merge commit `c80e0e4`.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Phần việc của tôi bảo đảm dữ liệu bài báo đi từ Crossref đến vector index theo một contract ổn định, có raw lineage để phục hồi, có lỗi giả lập để kiểm tra observability, và có ba không gian vector độc lập để so sánh chất lượng RAG giữa các trạng thái.

### Cách triển khai

Ingestion gọi Crossref REST API với query `agentic retrieval augmented generation large language model`, lấy tối đa 24 records có abstract và giới hạn theo ngày xuất bản. Request có retry/backoff cho các HTTP status tạm thời như 429 và 5xx. Khi live API thất bại, pipeline có thể đọc snapshot raw đã lưu.

Parser dùng DOI làm `paper_id`, chuẩn hóa title, abstract, authors, categories và ngày Crossref; loại JATS/XML tags và HTML entities; bỏ records thiếu DOI, title hoặc abstract; deduplicate theo DOI.

Cleaning tạo dataframe có thứ tự ổn định theo `published` giảm dần và `paper_id`. Trường `text_for_embedding` luôn gồm năm phần: Title, Authors, Published, Categories và Summary. `age_days` được tính từ cùng một `run_date` để baseline và repair tái hiện được.

Retrieval sử dụng OpenAI `text-embedding-3-small` và ChromaDB với cosine distance. Provider/model được ghi trong manifest; lúc load index, cấu hình hiện tại phải khớp manifest để tránh truy vấn một collection bằng vector khác kích thước. Baseline, corrupted và repaired dùng ba collection riêng.

Corruption được chọn theo thứ tự record cố định để hai máy tạo cùng kết quả. Repair không chỉnh sửa dataframe lỗi mà đọc lại raw records, chạy lại cleaning và rebuild index.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | Crossref JSON response hoặc `data/raw/crossref_response.json` |
| Raw output | `PaperRecord` gồm DOI, title, summary, authors, categories, dates và URLs |
| Clean output | 16 cột theo clean-data contract, gồm `age_days`, `summary_chars`, `text_for_embedding` |
| Vector output | ChromaDB collection và JSON manifest cho mỗi trạng thái |
| Module phụ thuộc | `core.config`, `core.utils`, OpenAI embeddings, ChromaDB |
| Module sử dụng output | Observability quality gate, test-set builder, evaluation metrics và reporting |
| Điều kiện lỗi | API timeout/429/5xx, malformed payload, missing required fields, empty dataframe, embedding manifest mismatch |

### Cách xác minh

```bash
uv run --extra dev pytest -q
```

- **Kết quả mong đợi:** parsing, cleaning, corruption/repair và Chroma contract đều pass.
- **Kết quả thực tế:** `6 passed`.
- **Artifacts:** `data/raw/`, `data/clean/`, `data/embeddings/`, `data/chroma/`, `data/results/corruption_log.json`.

Smoke test live đã xác minh:

- Crossref trả 24 records và clean dataframe có 24 dòng.
- `text-embedding-3-small` build được Chroma index.
- Semantic search trả top result theo query thử nghiệm.
- Ba collection có số lượng lần lượt 24, 22 và 24 documents.
- Repaired dataframe bằng baseline dataframe trên cùng raw source và run date.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Starter repo hard-code `sentence-transformers/all-MiniLM-L6-v2`, trong khi nhóm muốn dùng OpenAI embedding.
- **Các phương án đã cân nhắc:** giữ MiniLM local; chuyển hoàn toàn sang OpenAI; hoặc hỗ trợ cả hai qua cấu hình.
- **Phương án đã chọn:** tạo factory `build_embeddings(settings)`, mặc định dùng OpenAI `text-embedding-3-small` và vẫn giữ local MiniLM làm phương án cấu hình được.
- **Lý do:** OpenAI embedding cho phép thống nhất provider với hệ thống đang dùng và không buộc các module gọi trực tiếp một implementation cụ thể. Local backend vẫn hữu ích khi cần phát triển offline.
- **Trade-off:** OpenAI cần mạng, API key và phát sinh chi phí theo token; vì vậy manifest phải ghi chính xác provider/model và raw snapshot vẫn được giữ để pipeline có thể tái hiện phần dữ liệu.
- **Bằng chứng:** cả indexing và querying dùng chung factory; manifest của cả ba trạng thái ghi `openai/text-embedding-3-small`; live smoke search trả kết quả thành công.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng:** Crossref live request trả `400 Bad Request` trong smoke test.
- **Bước tái hiện:** gọi `/works` với tham số `select` ban đầu có `updated` và `comment`.
- **Nguyên nhân gốc:** Crossref route `/works` không hỗ trợ hai field này trong danh sách `select`.
- **Cách xử lý:** loại `updated` và `comment` khỏi request `select`; parser vẫn xử lý chúng khi snapshot hoặc payload khác có cung cấp.
- **Cách xác minh sau khi sửa:** live request trả 24 items, parser tạo 24 records và lưu raw response thành công.
- **Điều học được:** schema response có thể chứa nhiều field hơn tập field mà endpoint cho phép chọn; request contract và response contract cần được kiểm tra độc lập.

## 7. Hiểu biết về luồng end-to-end

1. Crossref response được giữ nguyên để bảo toàn lineage. Parser chuyển từng item thành `PaperRecord`; cleaning chuẩn hóa schema và tạo `text_for_embedding`; OpenAI tạo vector và ChromaDB lưu vector cùng metadata.
2. Evaluation set lưu câu hỏi, ground truth và `ground_truth_doc_ids`. Retrieval hit khi danh sách paper IDs lấy được chứa đúng ground-truth ID; answer quality được đo bằng Token F1 và LLM Judge.
3. Quality checks kiểm tra cấu trúc và nội dung hiện tại như row count, null, uniqueness và summary length. Freshness monitoring tập trung vào tuổi dữ liệu thông qua `age_days` và tỷ lệ records vượt ngưỡng 180 ngày.
4. Cùng test set phải được dùng cho baseline, corrupted và repaired để metric thay đổi phản ánh thay đổi dữ liệu/index thay vì thay đổi câu hỏi.
5. Repair thành công khi dữ liệu được dựng lại từ raw source, quality/freshness trở lại trạng thái hợp lệ, repaired index được rebuild, và metrics tiến gần hoặc bằng baseline. Trong phần Data/RAG, repaired dataset hiện khớp baseline và collection count trở lại 24.

## 8. Phân tích kết quả

### Metrics chính

| Metric/signal | Baseline | Corrupted | Repaired | Nhận xét cá nhân |
| --- | ---: | ---: | ---: | --- |
| `retrieval_hit_rate` | 1.0000 | 0.7000 | 1.0000 | Corruption giảm 0.30; repair phục hồi hoàn toàn |
| `mean_token_f1` | 1.0000 | 0.3201 | 1.0000 | Câu trả lời sai/nhiễu làm F1 giảm mạnh |
| `judge_accuracy` | 1.0000 | 0.5000 | 1.0000 | Theo metrics đã commit |
| `mean_judge_score` | 5.0000 | 3.0000 | 5.0000 | Theo metrics đã commit |
| GX critical gate | Pass | Fail | Pass | Corruption bị quality gate chặn |
| Freshness stale ratio | 0% | 31.82% | 0% | Corrupted vượt SLA 25% |
| Dataset rows | 24 | 22 | 24 | Repair phục hồi row count và nội dung baseline |
| Chroma documents | 24 | 22 | 24 | Ba trạng thái được cô lập thành ba collection |

### Kết luận từ số liệu hiện có

1. `drop_latest_records` loại 5/24 records; blank summary, noise và title truncation mỗi loại tác động 2 DOI; stale date tác động 7 DOI; duplicate rows thêm 3 dòng. Corrupted còn 22 dòng, Hit Rate giảm 0.30 và Token F1 giảm khoảng 0.68. Đây là tác động tổng hợp của suite, không quy toàn bộ mức giảm cho một lỗi riêng lẻ.
2. Repair đọc lại `crossref_records.json`, chạy cùng cleaning contract và rebuild `papers-repaired`. Repaired có 24 dòng; data fingerprint, Hit Rate và Token F1 khớp baseline. Quality gate từ Fail trở lại Pass, stale ratio từ 31.82% về 0%.
3. Bonus self-healing thực hiện tự động phát hiện → cách ly → dựng lại → kiểm định → publish staged index; lượt drill sau merge kết thúc `HEALTHY`. Dashboard dùng SSE hiển thị trạng thái và artifacts theo thời gian thực.

Test set chuẩn hiện không có câu `categories` vì raw Crossref không cung cấp `subject`; tôi không coi đây là mục đã đạt đủ Guide. Artifact hard benchmark 30 câu được đánh giá riêng để tránh exact-title leakage của bộ 10 câu.

## 9. Điều học được và hướng cải thiện

### Ba điều quan trọng nhất

1. Raw preservation là điều kiện để repair có thể tái hiện và kiểm chứng, thay vì sửa tay dữ liệu lỗi.
2. Embedding model là một phần của data contract; index và query phải dùng cùng provider, model và vector dimension.
3. Corruption chỉ có ý nghĩa khi ghi được affected IDs và đánh giá ba trạng thái bằng cùng test set.

### Nếu có thêm thời gian

Tôi sẽ bổ sung batching và cache cho OpenAI embeddings, cùng retry có jitter và usage logging. Cải thiện sẽ được đo bằng số API calls, tổng token embedding, thời gian build index và khả năng tiếp tục sau lỗi mạng mà không phải embed lại toàn bộ documents.

## 10. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Mọi kết luận hiện có đều có artifact hoặc kết quả thực thi để đối chiếu.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Trần Nguyễn Tiến Đức  
**Ngày cập nhật sau tích hợp:** 2026-09-25 — cần tự rà soát bản cập nhật trước khi nộp.
