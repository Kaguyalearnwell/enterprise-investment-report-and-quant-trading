# 修正版 Prompt（Prompt 04）與現有程式碼差異分析

> 對象：使用者 2026-11-27 提供之「機構級企業研究、成長評級、進場時機與個人量化策略回測系統」Prompt（以下稱 P04）。
> 比對基準：GitHub 分支 `claude/trusting-gauss-pam41z` 最新 commit（Stage 2 完成，115 項測試通過）。
> 本文件只做分析，**尚未修改任何程式**。

---

## 結論摘要

| 類別 | 項數 | 說明 |
|---|---|---|
| A. 與現有程式一致，不需修改 | 14 | 策略公式、週線、消融版本、時序、原始／還原價分離、資料庫原則 |
| B. 現有程式需要小幅修改 | 4 | 成交量兩種定義、滑價區間、PASS/WARNING/FAIL 判定、舊文件標記作廢 |
| C. 與現有規格有差異，需要你決定 | 4 | 停損價格基礎、BBW 百分位視窗、目錄架構、處置／全額交割股資料 |
| D. P04 新增、目前完全沒有的模組 | 8 | 基本面資料、企業與成長評級、估值、進場整合、報告、個股／全市場回測分離、Dashboard 新頁面 |

**最大的變化不是量化策略，而是新增了「基本面研究、評級與估值」整個系統。**量化策略部分，P04 和 repo 中已確認的 Phase 1 規格幾乎完全一致。

---

## A. 已一致，不需修改（逐項核對 P04 §8、§9.3）

| P04 規格 | 現有程式 | 位置 |
|---|---|---|
| MA13/34/55 = SMA | `sma()` | `backend/indicators/core.py` |
| `MA55[t] > MA55[t-5]` | `(MA55[t]−MA55[t−5])/5 > 0`（正負號相同，數學上等價） | `backend/strategies/fib_bb_weekly.py:56` |
| BB 20、2σ、BBW 公式 | 母體標準差（ddof=0，與 TradingView 一致） | `indicators/core.py` |
| Squeeze ≤ 20%、最近 6 日至少一次 | 相同（含當日） | `fib_bb_weekly.py:69` |
| Breakout：Close > BB Upper | 相同（還原價） | — |
| Liquidity：SMA(Close×Volume,20) ≥ 2,000 萬 | 相同（原始價、股數） | — |
| WeeklyTrend 公式、只用上一個已完成週 K | 相同；SIGKILL 以外另有「擾動測試」證明無前視 | `indicators/weekly.py`、`tests/test_no_lookahead.py` |
| 消融版本 A、B、C、D、E、S0 | **完全相同**（S0 = Squeeze + Breakout + Volume） | `fib_bb_weekly.py:35-42` |
| ATR = Wilder ATR(14) | 相同（與 Pine `ta.atr` 逐筆一致） | `indicators/core.py` |
| Initial Stop = Entry − 2.5 ATR | 相同 | `strategy_default.toml`、Phase 1 §4 |
| Trailing = 進場後最高收盤 − 3 ATR | 相同（只上移） | Phase 1 §4 |
| Trend Exit：連續 2 日收盤 < MA34 | 相同 | 設定 `trend_exit_bars = 2` |
| Time Stop：40 日未達 1R | 相同 | 設定 `time_stop_bars = 40` |
| t 日收盤確認、t+1 開盤進場；漲停不成交；跌停延遲出場 | Phase 1 §4 規格（撮合引擎 Stage 4 實作） | `docs/phase1_math_spec.md` |
| IS/Val/OOS = 60/20/20，時間序列切分 | 相同 | `strategy_default.toml [split]` |
| 歷史股票池含下市股、揭露存活者偏誤 | Stage 2 已實作並揭露限制 | `docs/stage2_report.md` |
| 資料保留來源、取得時間、版本；不得虛構 | 每列有 `source`、`ingested_at`；`data_revisions`、`data_versions` | `backend/database/` |
| 不開發券商 API、下單 | 未開發，也不會開發 | — |

---

## B. 需要小幅修改的現有程式

### B1. 成交量確認：同時支援兩種定義（P04 §8.2）

- **現況**：只有定義 A（20 日均量**包含**當日），`fib_bb_weekly.py:73`。
- **P04 要求**：同時測試 B（前 20 個完整交易日、**不含**當日）。
- **注意**：repo 內的 Stage 1 研究設計原本用的是 B（`docs/stage1_strategy_research.md:217`），Phase 1 改成 A 以配合 TradingView。兩者的實際門檻不同：A 等價於「當日量 > 前 19 日均量的 1.54 倍」。
- **修改**：設定檔新增 `volume_ma_mode = "include_today" | "prior_20"`，訊號同時輸出兩個欄位，消融實驗兩種都跑。約 20 行程式 + 測試。

### B2. 滑價情境改為區間（P04 §9.4）

| 情境 | 現有設定 | P04 |
|---|---|---|
| 樂觀 | 3 bps | 3–5 bps |
| 基準 | 5 bps | 5–15 bps |
| 壓力 | 15 bps | **15–30 bps** |

- **修改**：每個情境改為上下限，回測時兩端都跑（壓力上限 30 bps 是新的）。手續費折數、證交稅 0.3%、不用當沖稅率已一致。

### B3. 資料驗證的 PASS / WARNING / FAIL 判定（P04 §14.2）

- **現況**：逐筆問題分為 `error / warning / info` 與 `exclude / flag / keep`，沒有「這份資料能不能用」的總判定。
- **修改**：在資料品質報告新增**每檔股票、每個資料集**的判定：有未解決的 `error` → FAIL（不得用於正式回測）；只有 `warning` → WARNING（揭露後可用）；其餘 → PASS。回測引擎在 Stage 4 必須讀取這個判定並排除 FAIL。

### B4. 舊研究文件與正式規格不一致，需標記為已被取代

`docs/stage1_strategy_research.md`（最早的研究設計）有 5 個參數與現行正式規格不同：

| 項目 | Stage 1 舊設計 | 現行正式規格（Phase 1 = P04） |
|---|---|---|
| 初始停損 | 2 ATR（或整理區低點） | 2.5 ATR |
| 移動停損基準 | 進場後最高「價」 | 進場後最高「收盤價」 |
| 趨勢出場 | 1 日收盤 < MA34 | 連續 2 日 |
| 時間停損 | 60 日 | 40 日 |
| BBW 百分位視窗 | 252 日 | 120 日 |

- **修改**：在該文件開頭加註「研究設計參考，參數以 Phase 1 規格為準」，避免日後混用（P04 §9.3 明確要求不得混用版本）。程式本身已使用正式規格，**不需要改程式**。

---

## C. 有差異、需要你決定

### C1. 移動停損的價格基礎 ⚠️ 最重要

- **P04 §8**：「流動性、撮合與**停損計算**使用原始價格」。
- **現行 Phase 1 規格**：移動停損的「進場後最高收盤」在**還原價**上追蹤，再換算成原始價下單。
- **為什麼 Phase 1 這樣設計**：持股期間遇到除息，原始收盤價會下跌一個股利，如果用原始價記錄最高收盤，停損線不會跟著下移，等於把「發放股利」誤判成「股價下跌」，造成提早出場。台股殖利率高，這種情況很常見。
- **兩種做法的實際成交都用原始價**，差別只在停損線怎麼算。
- **建議**：維持 Phase 1 做法（實際觸發價為原始價，追蹤基準扣除除權息影響），並在回測報告中同時提供「純原始價」版本作為比較。

### C2. BBW 百分位的視窗是否包含當日

- **P04**：「過去 120 個交易日的 BBW 百分位」。
- **現行**：與當日比較的是**前** 120 日（不含當日），與 TradingView `ta.percentrank` 一致。
- 差異很小；**建議維持**，以確保 Python 與 TradingView 訊號可以逐日比對。若你要求含當日，改一個參數即可。

### C3. 目錄架構

- P04 建議 `enterprise_investment_system/{data, research, valuation, quantitative, dashboard, config, tests, docs}`。
- 現有架構剛在 Stage 2 依前一份平台規格遷移為 `backend/{data, database, indicators, strategies, ...}`。
- **建議不再搬移**，而是新增 `backend/research/`、`backend/valuation/`，對應關係：

| P04 建議 | 現有／新增位置 |
|---|---|
| `data/collectors, normalization, validation, storage` | `backend/data/collectors, normalizers, validators`、`backend/database/` |
| `quantitative/indicators.py, weekly_filter.py, signal_generator.py` | `backend/indicators/core.py, weekly.py`、`backend/strategies/fib_bb_weekly.py` |
| `quantitative/backtester.py, performance.py, statistical_tests.py` | 新增 `backend/backtesting/`、`backend/statistics/`（Stage 4–5） |
| `research/company, industry, financial, growth, reports` | 新增 `backend/research/` |
| `valuation/*` | 新增 `backend/valuation/` |
| `dashboard/` | `frontend/`（React + Vite，已決定） |

### C4. 處置股、全額交割股（P04 §9.1「必須考慮」）

- 免費資料沒有（Stage 2.1 實測：FinMind 該資料集需付費；TWSE 官網封鎖開發環境）。
- 選項：(a) 購買 FinMind Backer；(b) 在你的電腦抓取證交所／櫃買中心公告；(c) 接受限制並在每份回測報告揭露。

---

## D. P04 新增、目前完全沒有的部分

### D1. 基本面資料管線（P04 §4、§13、§14.3）

FinMind 免費資料集（官方文件，**尚未實測**）：綜合損益表、資產負債表、現金流量表、月營收、股利政策、PER/PBR。

**重要發現（官方文件）：這些資料都沒有「正式公告日期」**，而 P04 要求「回測時不得使用當時尚未公布的財報」：

| 資料 | FinMind 日期欄位的意義 | 處理方式（建議） |
|---|---|---|
| 財報三表 | `date` = **季末日**，不是公告日 | 依法定申報期限推定可用日：Q1 → 5/15、Q2 → 8/14、Q3 → 11/14、年報 → 3/31 的**下一個交易日**（保守規則，需實測驗證） |
| 月營收 | `date` = 營收月份的次月 1 日；`create_time` 自 2026-04-21 起才有值，且是 FinMind 收錄時間 | 依法定期限推定：次月 10 日後的下一個交易日 |
| 財報修正 | FinMind 保存「公布當下的原始值」，但**沒有修正版本歷史** | 不能完整滿足 P04 §14.3「保存修正前後版本」；需揭露限制 |
| EPS | 單季值；配股後舊季不回溯調整，跨季相加會對不上 | 依文件建議，以淨利相加再除以股數 |

### D2. 企業研究與評分（P04 §3、§5、§6）

- 量化部分可由資料計算：財務比率、杜邦分析、盈餘品質檢驗（應收／存貨／現金流與淨利背離）、五年 CAGR。
- **質化部分無法單靠資料計算**：商業模式、護城河、TAM、競爭格局。系統只能儲存「分數 + 依據 + 來源 + 評分日期」，由研究流程（例如你的 Prompt 01）填入；**缺資料必須降低信心，不得補分**。
- 新增資料表：財務資料（含推定可用日）、月營收、評分紀錄、評級結果（含資料完整度與信心）。

### D3. 四個獨立評級（P04 §6、§11）

Company、Growth、Valuation、Entry Timing 分開計算；權重依 P04 表格寫入設定檔；最終 A–E 綜合判斷，**技術訊號不得把 E 級企業升級**（需寫成測試）。

### D4. 估值模組（P04 §7）

相對估值（同業與自身歷史；**缺歷史資料不得虛構百分位**）、DCF、Reverse DCF、Bear/Base/Bull、機率加權合理價、安全邊際。所有假設存入資料庫並版本化。

### D5. 進場時機整合（P04 §10）

P04 §10.3 的 6 種情境矩陣。「相對大盤強度」只作為觀察資訊，**不加入策略條件**（P04 要求不得擅自改策略）。

### D6. 回測：個股與全市場分開（P04 §9.8），以及新增指標（§9.5）

原規劃已有全市場投組回測；需新增：
- 個股回測模式（獨立報表，不與全市場混用）；
- 勝率**信賴區間**（建議 Wilson 區間）、損益兩平筆數、最大單筆獲利／虧損、最長連勝／連敗、依股票／產業／年份的勝率；
- 每日全市場訊號數量。

### D7. 報告輸出（P04 §15）

20 節固定格式與 7 個最終問題；需新增報告產生器。

### D8. Dashboard 新頁面（P04 §12）

在原規劃 7 頁之外，新增：股票總覽（四個評級）、企業研究、估值分析。

---

## E. 對開發順序的影響（建議）

| 原計畫 | 建議新順序 |
|---|---|
| Stage 3 指標與策略 | **Stage 3** 指標與策略（含 B1 成交量兩種定義） |
| Stage 4 回測與模擬 | **Stage 4** 回測與模擬（含 B2、B3、D6） |
| Stage 5 統計驗證 | **Stage 5** 統計驗證 |
| — | **Stage 6（新）** 基本面資料管線（D1，先做 Stage 2.1 式實測驗證） |
| — | **Stage 7（新）** 企業／成長評級與估值（D2–D4） |
| — | **Stage 8（新）** 進場時機整合與報告（D5、D7） |
| Stage 6 Dashboard | **Stage 9** Dashboard（含 D8） |
| Stage 7 整合驗收 | **Stage 10** 整合驗收 |

理由：量化策略回測是 P04「必要功能」，且資料管線已完成；基本面模組需要新的資料驗證（公告日推定規則必須先實測），放在後面不會阻塞策略研究。若你更重視企業研究，也可以把 Stage 6–7 提前。
