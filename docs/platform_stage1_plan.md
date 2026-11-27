# 台股全市場量化研究與投資決策輔助系統 — Stage 1：架構與開發計畫

> 規格來源：使用者 2026-11-27 提供之「Full Quantitative Research & Portfolio Simulation Platform」Prompt（以下稱「平台規格」）。
> 本文件只做規劃，**不新增程式碼**。標示 🔶 的項目需要使用者確認後才能進入對應階段。

---

## 0. 定位（摘要）

- 研究、分析、模擬、報表平台。**不做任何真實交易**：不串接券商 API、不下單、不讀取真實帳戶、不自動交易。
- 保留完整的**歷史模擬撮合引擎**（成本、漲跌停、跳空、停牌、零股、資金限制），不因「不下單」而簡化。
- Python 是唯一的資料、運算、回測平台；TradingView 只做視覺化與人工比對。

---

## 1. 既有成果盤點（不覆蓋，沿用並遷移）

| 既有成果 | 狀態 | 位置 | 在新平台中的角色 |
|---|---|---|---|
| Stage 1 研究設計（2×2 因子、隨機基準、DSR/PBO） | ✅ 文件 | `docs/stage1_strategy_research.md` | 平台規格 §9 的方法依據 |
| Phase 1 數學規格與邏輯審核 | ✅ 文件 | `docs/phase1_math_spec.md` | 策略公式的唯一來源 |
| 指標（SMA、母體標準差、Wilder ATR、Percent Rank、BB） | ✅ 已測試 | `src/indicators.py` | `backend/indicators/` |
| 週線濾網（上一完成週、實際交易日曆、無 repaint） | ✅ 已測試 | `src/weekly_filter.py` | `backend/indicators/weekly.py` |
| 訊號產生、A→E + S0、共同可交易遮罩 | ✅ 已測試 | `src/signal_generator.py` | `backend/strategies/` |
| 資料來源實測驗證（FinMind/TPEx/TWSE） | ✅ 報告 + 證據 | `docs/stage2_1_data_sources.md` | `data_source_registry` 初始內容 |
| SQLite schema v1（13 張表）、遷移、修訂追蹤 upsert | ✅ 已測試 | `src/database/` | `backend/database/`（擴充為 v2） |
| Provider 介面、FinMind Provider、可續傳下載器、CLI | ✅ 已測試 + 實測 | `src/data_providers/`、`src/data/`、`main.py` | `backend/data/` |
| 自動化測試 | ✅ 68 項通過 | `tests/` | `backend/tests/` |

**實測紀錄（Stage 2.3，2026-11-27）**：參考資料同步成功（3,631 檔證券、日曆、公司行動、報酬指數）；單檔 2330 共 3,394 日與交易日曆完全一致；11 檔混合測試（含下市、長期停牌、不在任何清單的 5326、上櫃股）全部成功；強制中斷（SIGKILL）後資料庫完整性檢查 `ok`。**續傳結果尚未完成驗證**（測試被中斷），列入 Stage 2 待辦。

**目錄遷移方式**：使用 `git mv` 把 `taiwan_quant_system/` 搬到平台規格 §13 的 `backend/` 結構，保留 git 歷史；所有既有測試在搬移後必須全數通過才算完成。

---

## 2. 技術選型

| 層 | 選擇 | 理由 |
|---|---|---|
| 後端 API | **FastAPI** + Uvicorn | 規格建議；型別化 API 自動產生 OpenAPI 文件，前端可直接產生型別 |
| 運算 | pandas、NumPy、SciPy（自建回測與撮合引擎） | 規格要求自建；不依賴 vectorbt/backtrader 的撮合假設 |
| 資料庫 | SQLite（WAL）→ 預留 PostgreSQL | 已完成抽象層（`DatabaseManager`）；SQLite 專屬語法集中在 schema 檔 |
| 前端 🔶 | **建議：React + TypeScript + Vite**，圖表 **ECharts** | 本系統是本機研究工具，不需要 Next.js 的伺服器渲染；Vite 打包成靜態檔由 FastAPI 直接提供，部署最單純。ECharts 原生支援 K 線、熱圖、大量資料縮放與手機觸控 |
| 表格 | TanStack Table（虛擬捲動） | 全市場 2,000+ 檔訊號表不卡頓 |
| 匯出 | CSV（內建）、XLSX（openpyxl）、PDF（WeasyPrint 由 HTML 報表轉出） | 三種格式共用同一份報表資料 |
| 部署 | `docker-compose.yml`（backend + 前端靜態檔），也提供不用 Docker 的啟動方式 | 使用者在自己電腦一鍵啟動 |

---

## 3. 系統架構

```
┌──────────────────────── 使用者電腦（建議） ────────────────────────┐
│                                                                   │
│  每日排程 ─► 資料管線 ─► SQLite ◄─ 指標/訊號 ◄─ 回測/模擬 ◄─ 統計驗證 │
│   (18:00 後)  下載→驗證→     │         as-of 時間戳                     │
│               還原→週線      ▼                                        │
│                         FastAPI  ──JSON──►  React Dashboard（瀏覽器）   │
│                                                                   │
│  TradingView（獨立）：Pine v6 視覺化，人工比對 Python 訊號清單          │
└───────────────────────────────────────────────────────────────────┘
       ▲ FinMind API / TPEx 官方（合法免費來源）  ✗ 券商 API（禁止）
```

**資料流原則**：每一層只讀上一層的輸出；任何決策欄位都帶 `as_of`（資料可用時點）；撮合引擎只能讀原始價與漲跌停表，訊號模組只能讀還原價與指標表。

---

## 4. 資料庫 Schema v2 規劃

平台規格 §6.3 要求的表名與既有 v1 的對應。原則：**不重複儲存同一份資料**；同一事實只存一處，其餘用 SQL View 提供規格要求的名稱。

| 規格表名 | 實作方式 | 說明 |
|---|---|---|
| security_master | 既有表 | — |
| security_history | **新表** | 名稱、市場別、代號變更的有效期間（SCD Type 2） |
| industry_classification | 既有 `industry_history` 改名 | `point_in_time` 標記回填值 |
| daily_prices | View：`daily_prices_raw` ⨝ `daily_prices_adj` | 原始價與還原價**實體上仍分開**，View 只供查詢 |
| weekly_prices | **新表**（由日線產生） | 含 `week_end`（實際最後交易日）與 `complete` 旗標 |
| trading_calendar | 既有 `market_calendar` 改名 | — |
| corporate_actions | 既有表 | 公司行動唯一事實來源 |
| dividend_history / stock_split_history / capital_reduction_history | View（依 `action_type` 篩選） | 避免三份資料不一致 |
| trading_status / price_limits | 既有表 | — |
| suspension_history / disposition_history | **新表**（期間型：起訖日） | 免費資料只能推斷停牌；處置股需付費或官方公告（見 §7） |
| technical_indicators | **新表** | 每檔每日指標值 + `as_of` + `indicator_version` |
| strategy_signals | **新表** | 每個條件的布林值（供「當日判斷過程」頁面）+ `signal_as_of`、`strategy_version_id` |
| strategy_versions | **新表** | 公式與參數的不可變快照（JSON + 雜湊），修改即產生新版本 |
| backtest_runs | **新表** | 策略版本、資料版本、期間、成本、風險設定、執行時間 |
| simulated_trades | **新表** | 每筆模擬交易（訊號日、成交日、價格、股數、成本、出場原因、R 倍數） |
| portfolio_daily / portfolio_positions / portfolio_cash | **新表** | 每日總資產、每檔持倉（含當日移動停損價）、現金流水 |
| performance_metrics | **新表** | 每次回測的指標（長表：run_id, metric, value, segment） |
| data_download_log | 既有 `download_log` 改名 | — |
| data_validation_log | 既有 `data_quality_issues` 改名 | — |
| data_source_registry | **新表** | 規格 §6.1 要求的 8 項來源資訊（來源、方式、取得時間、涵蓋期間、欄位、限制、頻率、完整性） |
| experiment_registry | **新表** | 每次實驗的預先登記門檻、參數組數（上限 432）、所有嘗試紀錄 |
| chip_daily / chip_broker / chip_margin / chip_import_log | **新表（可選模組）** | 每列帶 `published_at` 與 `available_from`（見 §6） |
| （既有）data_revisions、benchmark_index、schema_migrations | 保留 | — |

**版本管理**：`data_version` = 該次回測時資料庫內最後一次修訂的時間戳 + 各資料集筆數雜湊；`strategy_version` = 公式與參數 JSON 的 SHA-256。回測紀錄只新增、不覆蓋。

---

## 5. 策略 Baseline 定義 🔶

平台規格要求：週線濾網公式若未定義，不得自行假設。你在先前的 Prompt 03 §7 已定義如下，**請確認是否作為正式 Baseline**：

```
WeeklyTrend_w = (W_SMA13 > W_SMA34 > W_SMA55) AND (W_SMA55_w > W_SMA55_{w-1})
日線 t 使用「t 所在週的上一個已完成週 K」的 WeeklyTrend
```

其他 Baseline 條件（平台規格 §3 已明確定義，沿用 Phase 1 實作）：

| 條件 | 公式 |
|---|---|
| 均線 | SMA13 > SMA34 > SMA55，且 (SMA55_t − SMA55_{t−5})/5 > 0 |
| 壓縮 | BBW 百分位（過去 120 日，不含當日）≤ 20%，最近 6 日內至少一次 |
| 突破 | Close > BB Upper（20, 2σ，母體標準差） |
| 量 | Volume > 1.5 × SMA20(Volume) |
| 流動性 | SMA20(收盤 × 成交量) ≥ NT$20,000,000（原始價） |
| 停損 | 進場價 − 2.5 × ATR14（訊號日 ATR）；移動停損 = 進場後最高收盤 − 3 × ATR14，只上移 |
| 出場 | 連續兩日收盤 < SMA34；持有 40 日未達 1R 時間停損；皆於次日開盤成交 |

**規格尚未定義、會影響 Baseline 的設定**（Phase 1 已提出研究預設值，需確認）：

| 項目 | 研究預設值 | 替代選項 |
|---|---|---|
| 追價限制 | 開盤價 > 前收 + 1 ATR 則取消 | 不設限；+3% |
| 投組初始風險 5% 的計算 | 停損上移到成本以上後，該部位風險計為 0 | 永遠以初始風險計 |
| 多檔同時訊號的排序 | 固定亂數種子（之後以相對強度做實驗） | 成交金額大者優先 |
| 零股 | 2020-10-26 前只能整股；之後允許零股，加 10 bps 滑價 | 一律整股 |
| 成本情境 | 樂觀 3 折/3 bps、基準 6 折/5 bps、壓力原價/15 bps | — |
| 樣本切分 | IS 60% / Validation 20% / OOS 20%，段間 40 日 embargo | Walk-Forward 另行報告 |
| 市場環境分類（§10.4，須事前定義） | 加權報酬指數 vs 其 200 日均線判多空；200 日內漲跌幅 < ±10% 為盤整；20 日年化波動在歷史前/後 30% 為高/低波動 | — |

---

## 6. 籌碼資料模組（可選，不進 Baseline）

- 不假設籌碼K線 APP 有 API 或批次匯出。**只有在 APP 確實提供合法 CSV/XLSX 匯出時**才寫匯入器；否則只提供手動匯入介面（上傳檔案 → 欄位對應 → 驗證 → 寫入 `chip_*`）。🔶 請告知 APP 是否有匯出功能。
- 每列記錄 `source`、`imported_at`、`published_at`；無明確發布時間時採「下一交易日才可用」（`available_from = t+1`）。
- 另一個合法來源選項：FinMind 免費提供三大法人買賣超、融資融券等資料集（需於該階段實測驗證權限與發布時間）。
- 實驗設計：Baseline vs Baseline + 單一籌碼條件，比較**增量 OOS** 表現，各自登記為獨立實驗。

---

## 7. 資料完整性：目前已知限制（會顯示在 Dashboard「資料品質」頁）

| 限制 | 影響 | 現況／替代方案 |
|---|---|---|
| TWSE 官網封鎖雲端環境 | 上市股無法官方交叉驗證、無上市日 | 在使用者電腦執行比對腳本 |
| 下市表不含合併／股份轉換（如 5326） | 存活者偏誤殘留 | TPEx 每月官方快照補齊；上市部分見上 |
| 471 檔下市股市場別未知 | 市場別統計不完整 | TPEx 快照比對可判定上櫃者，其餘標記 |
| 停牌、處置股、全額交割股（免費層無） | 撮合限制不完整 | 無價日推斷停牌；處置/全額交割需付費或官方公告 |
| 歷史產業分類（只有目前分類） | 產業 25% 上限與產業分析有前視偏誤 | 以目前分類回填並標記 `point_in_time = 0` |
| 除權息「權息」合併值無法拆分 | 成交量還原不精確（價格還原不受影響） | 取 FinMind 股利政策表拆分（待驗證） |

---

## 8. 執行環境與資料存放 🔶（最重要的決策）

雲端開發環境是**暫時的**：閒置後會被回收，0.85 GB 的全市場資料庫無法保存，也不能放進 git。Dashboard 也需要一台持續運作、每日更新的機器。

| 方案 | 說明 | 建議 |
|---|---|---|
| **A. 使用者電腦** | 我在這裡開發與測試（小規模真實資料 + 單元測試），push 到 GitHub；你在電腦上 `git pull`、建立資料庫、每日排程更新、開啟 `http://localhost` 使用 Dashboard | ✅ 建議。TWSE 官網通常也能從家用網路連線 |
| B. 自備雲端主機 | 同 A，但跑在 VPS 上，可從手機瀏覽 | 需自行管理主機與存取權限 |
| C. 只在本環境 | 每次工作階段重新下載 | ❌ 不適合（每次 8 小時） |

---

## 9. 開發階段計畫

每個階段：先提出該階段的細部計畫 → 實作 → 執行測試 → 回報完成內容、測試結果、未解決問題。

| 階段 | 內容 | 驗收（摘要） | 與既有工作的關係 |
|---|---|---|---|
| **Stage 1** | 本文件 | 使用者確認 🔶 項目 | — |
| **Stage 2** 資料管線 | 目錄遷移；Schema v2；完成續傳驗證；資料品質驗證模組；歷史股票池稽核（TPEx 快照）；週線表；資料版本；data_source_registry；**10 檔真實資料端到端驗收** | 規格 §15.1 全部項目；全市場下載腳本可在使用者電腦執行 | 延續 Stage 2.1–2.3（約 50% 已完成） |
| **Stage 3** 指標與策略 | 指標表、訊號表（每條件布林值 + as-of）、策略版本；單一股票驗證後擴展全市場 | §15.2 指標、日線訊號、週線無前視 | Phase 1 程式搬移與擴充 |
| **Stage 4** 回測與模擬 | 撮合引擎（漲跌停、跳空停損、停牌、零股、成本、資金不足、排序）、投組與風險限制、每日狀態 | §15.2 進出場時間、停損、成本、風險限制 | 依 Phase 1 §4–§6 規格 |
| **Stage 5** 統計驗證 | 2×2、2×2×2、A→E、LOO、隨機基準（配對交易次數/持有期/曝險）、Phi/MI 重複性、OOS、Walk-Forward、DSR、PBO（CSCV）、前視與存活者稽核、試驗登記 | §15.3 全部項目 | 依 Stage 1 研究設計 |
| **Stage 6** Dashboard | 7 個頁面 + 匯出；FastAPI 端點；前端 | §15.4 全部項目（以 Playwright 自動化測試） | 新 |
| **Stage 7** 整合驗收 | 全市場完整資料（在使用者電腦）、數據一致性檢查、README、安裝手冊、使用手冊、限制說明 | 全部驗收清單 | — |
| TradingView | Pine v6 指標（Stage 3 之後） | 同參數下與 Python 訊號清單人工比對 | Phase 7 規劃 |

**不會做的事**：券商 API、真實下單、真實帳戶、自動交易；以合成資料產生正式績效；在 OOS 不理想後擴大參數搜尋。

---

## 10. 使用者決定（2026-11-27）

| # | 項目 | 決定 |
|---|---|---|
| 1 | 週線濾網 Baseline | ✅ 沿用 Prompt 03：`W_SMA13 > W_SMA34 > W_SMA55` 且 `W_SMA55_w > W_SMA55_{w-1}`，使用上一個已完成週 K |
| 2 | 執行環境 | ✅ 方案 A：使用者電腦。本環境負責開發、單元測試與小規模真實資料測試 |
| 3 | 前端 | ✅ React + TypeScript + Vite + ECharts，由 FastAPI 提供靜態檔 |
| 4 | 籌碼K線 APP | ✅ 沒有匯出功能 → 只做手動匯入介面（不寫爬蟲、不用非官方 API）；另評估 FinMind 免費三大法人／融資融券資料集作為合法來源 |
| 5 | §5 研究預設值（追價限制、OpenRisk、排序、零股、成本、切分、市場環境分類） | ⏳ 尚未確認：開發可先使用，**正式 Baseline 回測前必須確認** |

## 11. Stage 2 細部計畫（待使用者同意後開始）

依序執行，每一步完成即測試與 commit：

1. **目錄遷移**：`git mv taiwan_quant_system/` → 平台規格 §13 的 `backend/` 結構；68 項既有測試全數通過。
2. **Schema v2**（migration 2，不修改 v1）：新增 §4 表格中的新表與 View；表名改為規格名稱；遷移測試。
3. **續傳驗證補完**：重做被中斷的 SIGKILL → 續傳測試，確認筆數一致、無重複。
4. **資料品質驗證模組**：OHLC、重複、缺漏交易日（相對日曆與無價日區分）、成交量／報酬異常、公司行動前後價格一致性；結果寫入 `data_validation_log` 並輸出報告。
5. **公司行動 → 還原價**：計算還原係數與 `daily_prices_adj`，並以「事件日前後收盤 vs 官方參考價」驗證。
6. **漲跌停價**：自算 + 事件日官方值；用 TPEx 官方「次日漲跌停價」抽樣驗證。
7. **交易狀態**：無價日 → 推斷停牌期間（`suspension_history`，標記 `inferred`）。
8. **歷史股票池稽核**：TPEx 每月快照比對，補齊合併類漏列（如 5326）、判定 471 檔未知市場別中的上櫃股；輸出倖存者偏誤報告。
9. **週線資料表**：由日線聚合、`week_end` 為實際最後交易日、未完成週標記；與 Phase 1 週線濾網交叉測試。
10. **data_source_registry 與資料版本**。
11. **10 檔真實資料端到端驗收**（含下市、停牌、除權息、分割、上櫃股），產出資料品質報告、覆蓋報告、Stage 2 驗收清單。
12. **使用者電腦安裝說明**與全市場下載指令（全市場下載由你在自己電腦執行）。
