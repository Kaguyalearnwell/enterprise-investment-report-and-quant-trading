# Stage 2.2：資料庫建置（Schema、索引、讀寫介面）

> **狀態：Stage 2.2 完成。資料庫結構與讀寫介面已建立並通過測試；資料庫內目前沒有任何真實市場資料**（真實資料下載屬於 Stage 2.3）。
> 程式：`taiwan_quant_system/src/database/`（`schema.py`、`migrations.py`、`db_manager.py`）
> 測試：`taiwan_quant_system/tests/test_database.py`（22 項）

---

## 1. 完成內容

| 項目 | 檔案 | 說明 |
|---|---|---|
| 資料表定義（Schema v1） | `src/database/schema.py` | 9 張必要資料表 + 3 張輔助表，全部使用 SQLite `STRICT` 型別檢查 |
| 版本化遷移 | `src/database/migrations.py` | 每個版本只執行一次、在交易中執行、以 SHA-256 檢查碼防止事後竄改 |
| 讀寫介面 | `src/database/db_manager.py` | 具「修訂追蹤」的 upsert、續傳用下載紀錄、時點正確的讀取函式 |
| 執行設定 | `config/settings.py`、`.env.example` | 路徑與 Token 只從環境變數讀取，Token 不會出現在程式碼或 `repr` |
| 資料來源登錄 | `config/data_sources.py` | 只記錄 Stage 2.1 已實測或文件確認的資料集、欄位、權限 |
| 日誌 | `src/utils/logger.py` | 統一 logging 格式（主控台＋檔案） |

---

## 2. 資料表一覽

### 2.1 必要資料表（使用者規格的 9 張）

| # | 資料表 | 主鍵 | 用途 | 與規格的差異／補充 |
|---|---|---|---|---|
| 1 | `security_master` | `stock_id` | 股票基本資料 | 新增 `listing_date_basis`（上市日是官方值還是「第一筆有價日」推定）、`first_price_date`、`last_price_date`（Stage 2.1 發現下市日 ≠ 最後交易日） |
| 2 | `daily_prices_raw` | `stock_id, date` | **未還原**日線 | `volume` 單位為**股**；`turnover` 為成交金額（NT$）；新增 `trade_count`（成交筆數）與 `has_price`（FinMind 以 0 表示無價日，存成 NULL + `has_price=0`） |
| 3 | `daily_prices_adj` | `stock_id, date` | **還原**日線（與原始價分開存放） | 新增 `price_factor`、`volume_factor`、`method`、`factor_as_of`，可追溯每個還原值怎麼算的 |
| 4 | `corporate_actions` | `stock_id, ex_date, action_type` | 除權息、減資、分割、面額變更 | 新增事件日官方 `before_price`、`reference_price`、`limit_up/down`，以及 `raw_payload`（原始 API 回應，JSON 格式檢查） |
| 5 | `price_limits` | `stock_id, date` | 每日漲跌停價 | 新增 `basis`：`computed`（自算）／`official_event`（事件日官方值）／`official_exchange`／`provider`，區分來源可信度 |
| 6 | `trading_status` | `stock_id, date` | 交易狀態 | 新增 `full_delivery_flag`（全額交割）與 `basis`：`official` 或 `inferred`（免費資料只能從無價日推斷停牌） |
| 7 | `industry_history` | `stock_id, effective_date` | 歷史產業分類 | 新增 `point_in_time`：0 表示「用目前分類回填」，避免把回填值誤當成歷史事實 |
| 8 | `market_calendar` | `date, exchange` | 交易日曆 | `session_type` 可標記 `saturday_makeup`（Stage 2.1 發現 2013 年後有 11 個週六交易日） |
| 9 | `download_log` | `dataset, stock_id, start_date, end_date` | 下載紀錄，續傳依據 | `stock_id='*'` 代表全市場請求；新增 `http_status`、`attempts`；狀態分為 `success／empty／failed／gated／partial` |

### 2.2 輔助資料表

| 資料表 | 用途 |
|---|---|
| `data_revisions` | **任何被覆蓋的舊值都會先保存在這裡**（資料表、主鍵、舊值、新值、修訂時間）。對應規格「不得使用未來修訂的資料取代當時可取得的資料而不作標記」 |
| `data_quality_issues` | 資料品質問題紀錄：檢查名稱、嚴重度、分類、處置（`keep／flag／exclude`）。對應規格「先分類、記錄原因，再決定是否排除」（Stage 2.4 使用） |
| `benchmark_index` | 加權／櫃買報酬指數（Stage 2.1 確認 `TaiwanStockTotalReturnIndex` 免費可用） |
| `schema_migrations` | 已套用的 schema 版本與檢查碼 |

### 2.3 索引

除了主鍵之外，另建：`daily_prices_raw(date)`、`daily_prices_adj(date)`（全市場單日截面查詢）、`corporate_actions(ex_date)`、`security_master(exchange, security_type)`、`download_log(status)`、`data_revisions(table_name, revised_at)`、`data_quality_issues(run_id, check_name)` 與 `(stock_id, date)`。

---

## 3. 重要設計決策（為什麼這樣做）

### 3.1 資料庫只擋「結構錯誤」，不擋「經濟異常」

- **會被拒絕**：日期格式錯誤（`2024/01/02`）、不在清單內的代碼（如交易所 `NYSE`）、型別錯誤（價格欄位放文字）、旗標不是 0/1、外鍵不存在、主鍵重複、`raw_payload` 不是 JSON。
- **不會被拒絕**：最高價 < 最低價、負成交量、異常跳空等。
- 原因：Stage 2 規格要求「不得直接刪除異常資料，應先分類、記錄原因」。如果資料庫在寫入時就拒絕，異常資料會在沒有任何紀錄的情況下消失。所以它們會先寫入，再由 Stage 2.4 的驗證模組標記到 `data_quality_issues`。（測試 `test_anomalous_values_are_stored_not_rejected`）

### 3.2 Upsert 具修訂追蹤，資料提供者的修改不會無聲覆蓋

每次寫入會先比對既有資料：

| 情況 | 處理 |
|---|---|
| 新的主鍵 | 新增 |
| 主鍵存在、內容相同（只有下載時間不同） | 跳過，不算修訂 |
| 主鍵存在、內容不同 | 先把舊值寫入 `data_revisions`，再更新 |

整批在單一交易中完成：只要其中一列違反限制，整批都不寫入（測試 `test_foreign_key_and_atomic_batch`）。

### 3.3 同一批資料的欄位必須一致（測試中抓到的 bug）

第一版 `upsert` 只用第一列的欄位，第二列以後若有額外欄位會被**無聲丟棄**（例如 3068 的下市日期）。測試 `test_universe_is_point_in_time` 抓到這個問題。修正後：欄位不一致的批次直接報錯，因為「丟欄位」或「用 NULL 覆蓋既有值」都會破壞資料。已加回歸測試 `test_heterogeneous_batch_rejected`。

### 3.4 時點正確（防止前視偏誤）的讀取函式

- `get_trading_days(start, end, as_of)`：**永遠不回傳 `as_of` 之後的日期**。Stage 2.1 發現 FinMind 交易日曆包含到 2027-12-30 的未來日期。
- `get_universe(as_of)`：只回傳「`as_of` 當天已上市、且尚未下市」的普通股；上市日未知時以第一筆有價日代替。**不會用今天的股票清單決定過去可以交易哪些股票。**

### 3.5 原始價與還原價分開

兩張不同的資料表，讀取函式也分開（`get_daily_prices_raw`／`get_daily_prices_adj`）。未來撮合引擎只會讀原始價與 `price_limits`，訊號模組才會讀還原價；這讓「不得用還原價判斷漲跌停與成交」可以在程式結構上被強制。

### 3.6 Schema 不可事後修改

每個 migration 都有檢查碼。若有人修改已套用過的 migration，系統會拒絕執行並要求新增一個新版本（測試 `test_edited_migration_is_refused`）。這確保資料庫結構和程式碼永遠一致。

### 3.7 未來轉換 PostgreSQL

欄位型別只用 TEXT／REAL／INTEGER，upsert 使用 `INSERT ... ON CONFLICT DO UPDATE`，兩者在 PostgreSQL 都可用。SQLite 專屬部分（`STRICT`、`GLOB` 日期檢查、PRAGMA、`json_valid`）全部集中在 `schema.py` 與 `DatabaseManager.__init__`，轉換時只需改寫 DDL 與連線層，回測引擎不需要改動（其他模組只透過 pandas DataFrame 與 `DatabaseManager` 溝通）。

---

## 4. 測試結果

```
$ cd taiwan_quant_system && python3 -m pytest -q
.............................................                            [100%]
45 passed in 1.71s
```

（23 項 Phase 1 測試 + 22 項 Stage 2.2 新測試。測試使用暫存資料庫與手寫的少量資料列，不是市場資料。）

| 測試 | 驗證內容 |
|---|---|
| `test_all_required_tables_exist`、`test_indexes_exist`、`test_pragmas` | 9 張必要表、索引、外鍵與 WAL 已啟用 |
| `test_migrations_are_idempotent`、`test_edited_migration_is_refused` | 重複執行無副作用；竄改舊版本被拒絕 |
| `test_primary_key_rejects_duplicates`、`test_duplicate_key_in_batch_is_rejected` | 主鍵唯一性 |
| `test_structural_errors_are_rejected` ×3、`test_enum_checks`、`test_corporate_action_payload_must_be_json` | 日期格式、0/1 旗標、STRICT 型別、代碼清單、JSON |
| `test_foreign_key_and_atomic_batch` | 外鍵；整批失敗時一列都不寫入 |
| `test_anomalous_values_are_stored_not_rejected` | 經濟異常保留給驗證模組處理 |
| `test_upsert_insert_unchanged_update_and_revision` | 新增／未變／更新三種情況計數正確；修訂紀錄內容正確 |
| `test_heterogeneous_batch_rejected`、`test_unknown_column_rejected` | 欄位不一致或不存在的欄位會報錯 |
| `test_nan_is_stored_as_null` | NaN 轉成 NULL |
| `test_download_log_attempts_and_resume` | 重試次數累加；只有成功／確認為空的請求會被視為已完成 |
| `test_trading_days_never_beyond_as_of`、`test_universe_is_point_in_time` | 時點正確讀取 |
| `test_token_never_in_repr` | Token 不會被印出 |

### 效能測試（合成資料，30 檔 × 3,629 日 = 108,870 列）

| 項目 | 結果 |
|---|---|
| 寫入速度 | 約 68,000 列／秒 |
| 重複寫入（內容未變，含比對） | 約 77,000 列／秒 |
| 讀取單一股票 12 年日線 | 約 8 毫秒 |
| 推估全市場（約 760 萬列） | 寫入約 2 分鐘、資料庫約 0.85 GB |

結論：瓶頸是 API 額度（約 8 小時），不是資料庫。

---

## 5. 尚未解決的問題

1. **資料庫內沒有真實資料**：Stage 2.3 才開始下載。
2. **上市日期**：FinMind 免費資料沒有上市日；TWSE 的 ISIN 網站在本環境被封鎖。目前設計以「第一筆有價日」推定，並用 `listing_date_basis` 標記。2013 年以前上市的股票不受影響（回測從 2015 年開始）。
3. **`raw_payload` 只存在公司行動表**：日線原始 API 回應是否另存壓縮檔（約 100 MB）將在 Stage 2.3 決定。
4. **PostgreSQL 轉換**尚未實作，只確保結構上可轉換。

---

## 6. 下一步：Stage 2.3（下載器）

依規格順序：建立 Data Provider 介面與 FinMind Provider → 測試單一股票 → 測試 10 檔 → 建立全市場可續傳下載機制（全域節流 600 次／小時、失敗重試、進度顯示、增量更新）。
