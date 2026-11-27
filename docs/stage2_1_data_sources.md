# Stage 2.1：資料來源確認（Data Source Verification）

> **狀態：Stage 2.1 完成。尚未下載任何正式資料，也尚未建立資料庫。**
> 證據：
> - 可重現的驗證腳本：[`taiwan_quant_system/scripts/verify_data_sources.py`](../taiwan_quant_system/scripts/verify_data_sources.py)
> - 實際 API 回應摘要：[`taiwan_quant_system/reports/stage2_1_data_source_verification.json`](../taiwan_quant_system/reports/stage2_1_data_source_verification.json)（執行時間 2026-11-27 03:22 UTC，未使用 token）
> - FinMind 官方文件 `finmind.github.io`（2026-11-27 讀取）與 FinMind Python SDK 原始碼（GitHub `FinMind/FinMind`，commit `d89a84a`，2026-11-22）

---

## 1. 結論摘要

1. **FinMind 免費層可以建立「單檔逐一下載」的全市場原型**，但不能一次下載整個市場的單日資料（該功能限 Backer/Sponsor）。
2. **已下市股票在免費 `TaiwanStockPrice` 中仍有歷史日線**（5 檔下市股抽查全部有資料）→ 倖存者偏誤**可以大幅降低，但不能宣稱已消除**。
3. **股票池必須合併 `TaiwanStockInfo` ∪ `TaiwanStockDelisting`**，且兩者仍不完整：TPEx 2013-03-01 官方行情中有 1 檔（5326 漢磊，2014 年股份轉換）不在任一清單中，但其價格資料存在。需要用官方歷史快照補齊。
4. **還原價（`TaiwanStockPriceAdj`）、漲跌停價（`TaiwanStockPriceLimit`）、暫停交易、處置股均為付費資料**。但可由免費的除權息、減資、分割、面額變更資料**自行計算還原係數與漲跌停價**，並用官方資料交叉驗證。
5. **TWSE 官方網站（www、isin）封鎖本雲端環境**（WAF 回傳 "FOR SECURITY REASONS"）；**TPEx 官方網站可用**。上市（TWSE）部分的官方交叉驗證需改由使用者電腦執行，或接受此限制。
6. 免費額度：**無 token 300 次/小時、免費 token 600 次/小時**。全市場（約 2,300 檔 × 2 種資料）約需 **8 小時（有 token）或 16 小時（無 token）**，下載器必須可續傳。

---

## 2. FinMind 資料集驗證矩陣

「實測」= 本環境實際呼叫 API 的結果；「文件」= 官方文件記載。

| 資料集 | 權限（實測） | 欄位（實測） | 涵蓋期間（文件） | 系統用途 |
|---|---|---|---|---|
| `TaiwanStockInfo` | ✅ 免費，全表一次下載 | `industry_category, stock_id, stock_name, type, date` | 每日更新 | security_master、產業（僅**目前**分類） |
| `TaiwanStockDelisting` | ✅ 免費，全表 | `date, stock_id, stock_name` | 2001-01-01 ~ | 下市日期 |
| `TaiwanStockTradingDate` | ✅ 免費，全表 | `date` | 1999-01-05 ~ **2027-12-30** | market_calendar |
| `TaiwanStockPrice`（單檔） | ✅ 免費 | `date, stock_id, Trading_Volume, Trading_money, open, max, min, close, spread, Trading_turnover` | 1994-10-01 ~ | daily_prices_raw |
| `TaiwanStockPrice`（全市場單日） | ❌ `Your level is free`（HTTP 400） | — | — | 付費後可大幅減少請求數 |
| `TaiwanStockDividendResult`（單檔） | ✅ 免費（全市場單日為付費） | `date, stock_id, before_price, after_price, stock_and_cache_dividend, stock_or_cache_dividend, max_price, min_price, open_price, reference_price` | 2003-05-01 ~ | corporate_actions（除權息） |
| `TaiwanStockCapitalReductionReferencePrice`（單檔） | ✅ 免費（全市場為付費） | `date, stock_id, ClosingPriceonTheLastTradingDay, PostReductionReferencePrice, LimitUp, LimitDown, OpeningReferencePrice, ExrightReferencePrice, ReasonforCapitalReduction` | 2011-01-01 ~ | corporate_actions（減資） |
| `TaiwanStockSplitPrice` | ✅ 免費，全表 | `date, stock_id, type, before_price, after_price, max_price, min_price, open_price` | 未記載 | corporate_actions（分割／反分割） |
| `TaiwanStockParValueChange` | ✅ 免費，全表 | `date, stock_id, stock_name, before_close, after_ref_close, after_ref_max, after_ref_min, after_ref_open` | 2020-01-01 ~ | corporate_actions（面額變更） |
| `TaiwanStockTotalReturnIndex` | ✅ 免費 | `price, stock_id, date` | 未記載 | 基準：加權／櫃買**報酬**指數 |
| `TaiwanStockPriceAdj` | ❌ 付費 | （文件）同 `TaiwanStockPrice` | 1994-10-01 ~ | 免費替代：自行計算 |
| `TaiwanStockPriceLimit` | ❌ 付費 | （文件）`date, stock_id, reference_price, limit_up, limit_down` | 2000-01-01 ~ | 免費替代：自行計算 |
| `TaiwanStockSuspended` | ❌ 付費 | （文件）`date, stock_id, suspension_time, resumption_date, resumption_time` | 2011-10-06 ~ | 免費替代：無價日推斷 |
| `TaiwanStockDispositionSecuritiesPeriod` | ❌ 付費 | （文件）`date, stock_id, stock_name, disposition_cnt, condition, measure, period_start, period_end` | 未記載 | 免費替代：TPEx 官方公告（待 2.5 驗證）；TWSE 部分缺失 |

付費資料集回應（實測，原文）：`{"msg":"Your level is free. Please update your user level. ...","status":400}`

---

## 3. 重要實測發現與對系統的影響

### 3.1 下市股票資料（倖存者偏誤）

| 股票 | 下市表日期 | 價格資料最後一日 | 在 `TaiwanStockInfo`？ |
|---|---|---|---|
| 3068 美磊 | 2018-06-13 | 2018-06-06 | 否 |
| 6022 大眾證 | 2017-08-28 | 2017-08-21 | 否 |
| 3291 遠翔 | 2016-10-06 | 2016-09-26 | 否 |
| 1333 恩得利 | 2020-11-17 | **2020-04-06**（停止交易 7 個月後才下市） | 否 |
| 5326 漢磊 | **不在下市表** | 2014-09-18 | 否 |

影響：
- 下市日 ≠ 最後交易日。回測必須用「最後有價日」處理出場，而非下市日。
- 1333 類型（長期停牌後下市）：持有中的部位會被**凍結數月後歸零或以最後價清算**，回測必須模擬，否則高估績效。
- 5326 類型（合併／股份轉換）：不在任何 FinMind 清單 → 需由官方歷史快照補入股票池。

### 3.2 TPEx 官方快照交叉驗證（普通股＝四碼代號）

| 日期 | TPEx 官方普通股數 | FinMind 清單缺漏 | 只能從下市表找到 |
|---|---|---|---|
| 2013-03-01 | 635 | **1（5326）** | 44 |
| 2016-01-04 | 705 | 0 | 36 |
| 2019-07-01 | 775 | 0 | 11 |
| 2022-07-01 | 799 | 0 | 0 |

結論：上櫃部分在抽查日期覆蓋率 ≥ 99.8%，但**若只用 `TaiwanStockInfo`（目前清單），2013 年會漏掉 7%（44/635）的上櫃股票**。**上市（TWSE）部分無法從本環境交叉驗證。**

### 3.3 價格欄位慣例（文件 + 實測）

- `Trading_Volume` 單位為**股**（2330 於 2024-01-02 = 27,997,826 股 ≈ 2.8 萬張）；`Trading_turnover` 是**成交筆數**，不是成交金額；成交金額為 `Trading_money`。
- 當日無公告成交價時，`open/max/min/close` 均為 **0**（文件）；可能「有量無價」，不能用 `Trading_Volume > 0` 判斷。→ 資料清理需將 0 價標記為「無價日」，不可當成價格。
- 興櫃股 `open` 為前日均價 → 本系統排除興櫃（`type == "emerging"`）。

### 3.4 股票池與市場別

- `TaiwanStockInfo` 4,400 列／3,191 個代號；1,082 個代號有多列（轉板保留舊列、產業總稱重複列）。**需取同一代號 `date` 最新的一列作為目前市場別**（文件）；產業需排除 `電子工業`、`化學生技醫療`、`創新板股票`、`創新版股票` 等非產業值（SDK 原始碼註解）。
- **沒有上市日期、下市日期、歷史產業欄位。** `date` 是資料更新日，不是上市日。
- `TaiwanStockDelisting` **沒有市場別欄位**（無法分辨下市或下櫃）。

### 3.5 交易日曆

- 7,177 個交易日，**包含 265 個未來日期（到 2027-12-30）** → 必須以「資料截止日」截斷，否則屬於前視資訊。
- 2013 年以來有 **11 個週六交易日**（補行上班日，例如 2016-01-30，2330 當日確有成交）。週線以 ISO 週計算可自然處理；**TradingView 是否顯示週六 K 棒需於 Phase 7 確認**。

### 3.6 公司行動 → 自行計算還原係數

| 事件 | 資料集 | 還原係數（向後還原） |
|---|---|---|
| 除權息 | `TaiwanStockDividendResult` | `reference_price / before_price` |
| 減資 | `TaiwanStockCapitalReductionReferencePrice` | `PostReductionReferencePrice / ClosingPriceonTheLastTradingDay` |
| 分割／反分割 | `TaiwanStockSplitPrice` | `after_price / before_price` |
| 面額變更 | `TaiwanStockParValueChange` | `after_ref_close / before_close` |

- 這些表也提供**事件當日的漲跌停價**（`max_price/min_price`、`LimitUp/LimitDown`、`after_ref_max/min`），可直接寫入 price_limits，不必自行推算。
- `ExrightReferencePrice = -1` 為缺值哨兵值，需轉為 NULL。
- 文件說明 FinMind 自家還原價「以最新交易日為基準回溯」；我們自建還原係數時，每個係數只在事件日之後才生效，符合 Phase 1 證明的尺度不變性。

### 3.7 漲跌停價 → 自行計算 + 官方驗證

- 一般交易日：`參考價 = 前一交易日收盤`；漲停 = `參考價 × 1.10` 依檔位向下取整，跌停 = `參考價 × 0.90` 依檔位向上取整（2015-06-01 前為 7%）。
- 事件日：使用 3.6 的官方參考價與漲跌停價。
- **驗證**：TPEx 官方每日行情含「次日漲停價／次日跌停價」欄位，可抽樣比對自算值（上櫃部分）。

### 3.8 TWSE / TPEx 官方網站可用性（本環境）

| 端點 | 結果 |
|---|---|
| `www.twse.com.tw` 歷史行情（rwd 與 exchangeReport 兩種路徑） | ❌ WAF 封鎖（HTTP 307 → "FOR SECURITY REASONS"） |
| `isin.twse.com.tw`（上市日期、ISIN） | ❌ 同一 WAF 封鎖頁 |
| `openapi.twse.com.tw` | ⚠️ 首次可用但**只提供最新一日**；第二次連線被重置 |
| `www.tpex.org.tw` 歷史每日行情 | ✅ 可用（含發行股數、次日漲跌停價） |

推測原因為雲端 IP 被證交所阻擋（未經證實）。不嘗試繞過。

### 3.9 速率限制

- 文件：無 token **300 次/小時**；註冊並驗證信箱的免費 token **600 次/小時**。
- 實測：首次測試在 1 秒內連續呼叫時，曾有免費資料集短暫回傳空資料（未取得錯誤訊息）；改為每次間隔 2 秒後重跑，全部正常。→ 下載器預設間隔 ≥ 2 秒，且以「每小時請求數」做全域節流。
- 文件記載的使用量查詢端點：`https://api.web.finmindtrade.com/v2/user_info`（回傳 `user_count`、`api_request_limit`，需 token）。

---

## 4. Stage 2 需求對照：資料來源、狀態、替代方案

| 需求 | 來源 | 狀態 | 缺失／限制 | 替代方案 |
|---|---|---|---|---|
| 上市股票清單 | `TaiwanStockInfo` (type=twse) | ✅ 可取得 | 無上市日 | 以第一筆有價日推定上市日 |
| 上櫃股票清單 | `TaiwanStockInfo` (type=tpex) | ✅ 可取得 | 同上 | 同上 |
| 下市股票 | `TaiwanStockDelisting` | ⚠️ 部分 | 無市場別；合併類漏列 | TPEx 官方快照補齊；TWSE 部分需使用者電腦 |
| 原始日線 | `TaiwanStockPrice` 單檔 | ✅ 可取得 | 每檔一次請求 | 付費 Backer 可改為每日一次 |
| 還原價 | 自行計算 | ✅ 可建立 | `TaiwanStockPriceAdj` 為付費 | 用 3.6 係數；付費後可交叉驗證 |
| 公司行動 | 4 個免費資料集 | ✅ 可取得 | 除權息、減資為單檔查詢 | 減資只查「出現無法解釋跳空」的股票 |
| 漲跌停價 | 自行計算 + 事件日官方值 | ✅ 可建立 | `TaiwanStockPriceLimit` 為付費 | TPEx 官方次日漲跌停價驗證 |
| 交易日曆 | `TaiwanStockTradingDate` | ✅ 可取得 | 含未來日期 | 截斷 + 與 2330 有價日交叉比對 |
| 交易狀態（停牌） | 無價日推斷 | ⚠️ 近似 | `TaiwanStockSuspended` 為付費 | 0 價日 = 無成交／停牌（無法區分原因） |
| 處置股 | — | ❌ 缺失 | 付費 | TPEx 官方公告（2.5 驗證）；TWSE 需使用者電腦 |
| 全額交割股 | — | ❌ 缺失 | 無免費來源已驗證 | 2.5 研究；否則標記限制 |
| 歷史產業分類 | `TaiwanStockInfo` | ❌ 只有目前分類 | 無歷史 | 使用目前分類並於報告揭露 |
| 股票代號變更 | — | ❌ 未驗證 | 台股代號變更少見 | 2.5 研究 |
| 基準指數 | `TaiwanStockTotalReturnIndex` | ✅ 可取得 | — | — |

---

## 5. 下載量與時間估計

- 股票池估計：`TaiwanStockInfo` 上市櫃四碼代號 2,164 檔 + 2013 年後下市且不在 Info 中者 + TPEx 快照補漏 ≈ **2,300 檔**。
- 每檔必要請求：日線 1 次（2013-01-01 至今一次取回）+ 除權息 1 次 ≈ **4,600 次**；減資僅對候選股查詢；分割、面額變更、日曆、清單為全表 1 次。
- 時間：600 次/小時 ≈ **8 小時**；300 次/小時 ≈ **16 小時**。因此下載器必須：全域節流、可中斷續傳、已完成區間跳過、每日增量更新（之後每天只需約 2,300 次 → 4 小時；付費 Backer 則每天 1 次）。

---

## 6. 倖存者偏誤（目前結論）

- **不得宣稱已消除。** 目前的證據只支持：「上櫃部分於 4 個抽查日覆蓋率 ≥ 99.8%；上市部分未驗證」。
- 已知的殘留偏誤來源：合併／股份轉換未列入下市表、上市部分無法官方比對、停牌期間無價格。
- Stage 2.5 將建立 `survivorship_audit`，以每月 TPEx 官方快照全面比對，並輸出覆蓋率報告。

---

## 7. 需要使用者決定的事項

1. **FinMind 免費 token（建議）**：註冊並驗證信箱後取得，額度由 300 提升到 600 次/小時，全市場下載時間減半。Token 不會寫入程式碼：可透過本環境的安全表單存放（由網路代理自動加入請求標頭），或在你自己的電腦放進 `.env` 的 `FINMIND_TOKEN`。
2. **是否購買 FinMind Backer**（選擇性）：可取得還原價、漲跌停價、停牌、處置股，以及全市場單日下載（每日 1 次請求）。這會解決 §4 大部分 ⚠️／❌ 項目，但不影響 Stage 2 的程式架構（Data Provider 介面可替換）。
3. **TWSE 官方資料**：本環境被封鎖。可選擇 (a) 接受「上市部分未經官方比對」的限制，或 (b) 之後在你的電腦執行一個比對腳本（我會提供）。

---

## 8. 下一步：Stage 2.2

建立 SQLite schema（使用者指定的 9 張表 + `adjustment_factors`）、主鍵／索引／唯一性限制、資料讀寫介面與測試。Stage 2.2 不需要下載資料，可以在你決定 token 之前完成。
