# 台股量化研究系統 使用說明書

> 版本：Stage 4 完成（2026-11-27）｜適用分支：`claude/trusting-gauss-pam41z`
> 安裝步驟的完整細節另見 [`docs/INSTALL.md`](INSTALL.md)。

---

## 目錄

1. [這套系統是什麼](#1-這套系統是什麼)
2. [目前能用的功能](#2-目前能用的功能)
3. [快速開始（30 分鐘）](#3-快速開始30-分鐘)
4. [建立全市場資料庫](#4-建立全市場資料庫)
5. [每日例行更新](#5-每日例行更新)
6. [指令手冊](#6-指令手冊)
7. [看懂輸出與報告](#7-看懂輸出與報告)
8. [查看資料庫裡的資料](#8-查看資料庫裡的資料)
9. [資料規則：使用前必須知道的事](#9-資料規則使用前必須知道的事)
10. [疑難排解](#10-疑難排解)
11. [已知限制](#11-已知限制)
12. [名詞解釋](#12-名詞解釋)

---

## 1. 這套系統是什麼

一套在**你自己電腦上執行**的台股（上市＋上櫃普通股）量化研究平台，用來：

- 蒐集並整理全市場歷史行情（含已下市股票）；
- 依量化策略篩選股票、做歷史回測、模擬 NT$1,000,000 的投資組合；
- 用統計方法檢驗策略是否真的有優勢；
- 以網頁 Dashboard 呈現研究結果。

**這套系統不會做的事**：不串接券商、不下單、不讀取你的真實帳戶、不自動交易。所有「交易」都是歷史模擬。回測結果不代表未來報酬。

---

## 2. 目前能用的功能

系統分階段開發，**目前完成 Stage 4（回測與投資組合模擬）**。開發順序見 `docs/prompt04_gap_analysis.md` §F。

| 功能 | 狀態 | 說明 |
|---|---|---|
| 下載股票清單、下市表、交易日曆、報酬指數 | ✅ 可用 | `download reference` |
| 下載日線（含下市股）、除權息、減資 | ✅ 可用 | 增量、可中斷續傳 |
| 還原價、漲跌停價、停牌推斷、週 K | ✅ 可用 | `process` |
| 資料品質檢查與報告 | ✅ 可用 | `validate` |
| 倖存者偏誤稽核（上櫃） | ✅ 可用 | `audit-survivorship` |
| 指標與策略訊號（SMA13/34/55、布林壓縮、週線濾網，A–E、S0，兩種成交量定義） | ✅ 可用 | `signals compute / list / explain` |
| 歷史回測、投資組合模擬（個股與全市場分開，含成本與風險限制） | ✅ 可用 | `backtest run / list / show` |
| 2×2 因子、消融、隨機基準、DSR、PBO 等統計驗證 | ⏳ 尚未開始 | Stage 5 |
| 基本面資料、企業與成長評級、估值、進場時機整合 | ⏳ 尚未開始 | Stage 6–8 |
| 網頁 Dashboard、報表匯出 | ⏳ 尚未開始 | Stage 9 |
| TradingView Pine Script 指標 | ⏳ 尚未開始 | Stage 3 之後 |
| 籌碼資料手動匯入 | ⏳ 資料表已建立，匯入介面尚未開發 | 你的 APP 無匯出功能，只會做手動匯入 |

**現階段你可以做的事**：建立全市場資料庫、每天更新、檢查資料品質，並查看每日符合策略條件的股票與每個條件的判斷過程。

---

## 3. 快速開始（30 分鐘）

### 3.1 安裝（只需一次）

需要：Python 3.11 以上、Git、FinMind 免費帳號（註冊並驗證信箱）。

```bash
git clone https://github.com/kaguyalearnwell/enterprise-investment-report-and-quant-trading.git
cd enterprise-investment-report-and-quant-trading
git checkout claude/trusting-gauss-pam41z
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -r requirements.txt
python -m pytest
```

最後一行應顯示 `115 passed`（或更多）。

### 3.2 設定 FinMind Token

```bash
cp .env.example .env        # Windows 用：copy .env.example .env
```

用記事本打開 `.env`，把 `FINMIND_TOKEN=proxy-injected` 換成你在 FinMind 會員頁取得的 token。

> ⚠️ Token 等同密碼：不要貼到聊天室、程式碼或任何公開地方。`.env` 已設定不會上傳到 git。

### 3.3 用 10 檔股票試跑

**以下所有指令都在專案根目錄、啟用虛擬環境後執行。**

```bash
python -m backend.cli init-db
python -m backend.cli registry
python -m backend.cli download reference
python -m backend.cli download prices    --stocks 2330 2317 3068 6022 1333 6488 5347 2881 1101 9929
python -m backend.cli download dividends --stocks 2330 2317 3068 6022 1333 6488 5347 2881 1101 9929
python -m backend.cli process  --stocks 2330 2317 3068 6022 1333 6488 5347 2881 1101 9929
python -m backend.cli validate --stocks 2330 2317 3068 6022 1333 6488 5347 2881 1101 9929
python -m backend.cli status
```

完成後打開 `reports/data_quality/` 最新的 `.md` 檔，就是這 10 檔的資料品質報告。

---

## 4. 建立全市場資料庫

第一次建立約需 **8 小時以上**（受 FinMind 每小時 600 次限制）。**可以隨時中斷**（關機、斷網、按 Ctrl-C），重跑同一個指令會自動從中斷處繼續，不會重複下載。

```bash
python -m backend.cli init-db
python -m backend.cli registry
python -m backend.cli download reference
python -m backend.cli audit-survivorship          # 約 10 分鐘，使用櫃買中心官方資料，不耗 FinMind 額度
python -m backend.cli download prices --all       # 約 2,300 次請求
python -m backend.cli download dividends --all    # 約 2,300 次請求
python -m backend.cli process --all
python -m backend.cli validate --all
python -m backend.cli signals compute --all      # 指標與訊號（全市場約 15 分鐘）
python -m backend.cli version "initial full load"
```

建議順序說明：

- `audit-survivorship` 放在下載日線之前，它會把 FinMind 清單漏掉的上櫃股票補進股票池，之後 `--all` 才會一起下載。
- `process` 必須在下載之後執行，因為還原價、漲跌停、週 K 都由日線與公司行動算出。
- `version` 會記下這次資料的「指紋」，之後的回測會引用它，確保結果可以追溯。

**看進度**：下載時每 10 檔會印一行進度，例如：

```
[prices] 120/2163 stocks | requests 118 | ok 118 empty 0 failed 0 skipped 2 | ETA 205 min
```

---

## 5. 每日例行更新

台股每日資料在 **台北時間 17:30** 後才定稿。請在 **18:00 之後**執行（若早於 18:00，系統只會更新到前一個交易日，這是刻意的保護）。

```bash
python -m backend.cli download reference
python -m backend.cli download prices --all
python -m backend.cli download dividends --all
python -m backend.cli process --all
python -m backend.cli validate --all
python -m backend.cli signals compute --all
python -m backend.cli signals list               # 最新一天的 E 版本研究訊號
python -m backend.cli version "daily update"
```

- 每次更新會重新下載最後 7 天，如果資料商修正了過去的數字，舊值會保存在 `data_revisions` 表，不會無聲覆蓋。
- 已下市且資料已完整的股票不會再下載。

**自動排程**（擇一）：

- **Windows**：「工作排程器」→ 建立基本工作 → 每天 18:30 → 動作「啟動程式」，指向一個包含上述指令的 `.bat` 檔。
- **macOS / Linux**：`crontab -e` 加入一行（路徑改成你的）：
  ```
  30 18 * * 1-5 cd /path/to/repo && .venv/bin/python -m backend.cli download prices --all >> reports/logs/cron.log 2>&1
  ```

---

## 6. 指令手冊

格式：`python -m backend.cli <指令> [選項]`。任何指令加 `--help` 可看說明。

### 6.1 資料庫與登錄

| 指令 | 作用 |
|---|---|
| `init-db` | 建立資料庫；若是舊版資料庫會自動升級（目前為 schema v3）。可重複執行 |
| `registry` | 寫入／更新「資料來源登錄」（來源、欄位、限制、驗證方式） |
| `status` | 顯示每張資料表的筆數，以及下載紀錄統計（成功、失敗、付費限制） |
| `version "說明文字"` | 記錄一個資料版本（各表筆數、最後修訂、雜湊值） |

### 6.2 下載

```
python -m backend.cli download <類型> [--stocks 代號 ... | --all] [選項]
```

| 類型 | 內容 | 需要 `--stocks/--all` |
|---|---|---|
| `reference` | 股票清單、下市表、交易日曆、分割／面額變更、加權與櫃買報酬指數 | 否 |
| `prices` | 未還原日線（開高低收、成交股數、成交金額、筆數） | 是 |
| `dividends` | 除權息結果（含官方參考價與當日漲跌停） | 是 |
| `capital-reductions` | 減資恢復買賣參考價 | 是（建議只查需要的股票） |
| `all` | `reference` + 全股票池的 `prices` + `dividends` | 否（自動全市場） |

| 選項 | 說明 | 預設 |
|---|---|---|
| `--stocks 2330 2317` | 指定股票 | — |
| `--all` | 股票池全部普通股（含下市股） | — |
| `--market TWSE TPEX UNKNOWN` | 搭配 `--all` 限定市場 | 三者全部 |
| `--limit N` | 只取前 N 檔（測試用） | 不限 |
| `--start 2013-01-01` | 起始日 | 2013-01-01 |
| `--end YYYY-MM-DD` | 結束日 | 最後一個已定稿交易日 |
| `--force` | 忽略下載紀錄，重新下載 | 關閉 |

> 起始日 2013-01-01 是刻意的：週線 SMA55 需要約 57 週暖機，才能從 2015 年開始回測。

### 6.3 策略訊號

| 指令 | 作用 |
|---|---|
| `signals compute --stocks ... / --all` | 計算指標與 A–E、S0 訊號；兩種成交量定義各存成一個策略版本 |
| `signals list [--date 日期] [--version E] [--volume-mode include_today] [--csv 檔名]` | 某天符合條件的股票（預設最新一天、E 版本）；可匯出 CSV（Excel 可直接開啟） |
| `signals explain 代號 日期 [--volume-mode ...]` | 該股票該天每個條件的規則、輸入值、是否成立 |

> 選股清單是**研究訊號**：t 日收盤後才成立，模擬進場最早是 `earliest_entry_date`（下一交易日）開盤。表中的「參考停損 = 收盤 − 2.5 × ATR」只是參考，實際停損以進場價計算。

成交量兩種定義（Prompt 04 §8.2）：`include_today` = 當日量 > 1.5 × 含當日的 20 日均量（正式定義，與 TradingView 一致）；`prior_20` = 當日量 > 1.5 × 前 20 個完整交易日均量。

### 6.4 歷史回測（模擬，不是真實交易）

| 指令 | 作用 |
|---|---|
| `backtest run --all [選項]` | 全市場投組回測（100 萬本金、所有風險限制） |
| `backtest run --stocks 2330 --mode single_stock` | 個股回測（獨立紀錄，不與全市場混用） |
| `backtest list` | 所有回測紀錄（每次執行都是新紀錄，不覆蓋舊的） |
| `backtest show 回測編號` | 該次回測的 In-Sample 績效；`--unseal` 才顯示 Validation／OOS |

| 選項 | 說明 | 預設 |
|---|---|---|
| `--version A/B/C/D/E/S0` | 策略版本 | E |
| `--volume-mode include_today / prior_20` | 成交量定義 | include_today |
| `--cost optimistic / base / stress` | 成本情境（手續費 3 折／6 折／原價） | base |
| `--slippage low / high` | 該情境滑價區間的下限或上限（樂觀 3–5、基準 5–15、壓力 15–30 bps） | low |
| `--basis adjusted / raw` | 移動停損基礎（預設在還原價追蹤，避免除息誤觸） | adjusted |
| `--start` / `--end` | 回測期間 | 2015-01-01 ～ 最新 |

> **為什麼預設看不到 OOS**：樣本外資料只能在預先登記評估標準後看一次，否則反覆依結果調整，OOS 就不再是獨立驗證。Stage 5 會建立登記流程。
>
> 資料品質判定為 FAIL、或從未驗證過的股票，會自動排除於回測之外；先執行 `validate --all`。

### 6.5 處理、驗證、稽核

| 指令 | 作用 | 輸出 |
|---|---|---|
| `process --stocks ... / --all` | 還原價、漲跌停價、上市日（興櫃判定）、停牌推斷、週 K | 資料庫 |
| `validate --stocks ... / --all` | 11 類資料品質檢查 | `reports/data_quality/dq-*.md`、`.json` |
| `audit-survivorship [--start] [--end]` | 與櫃買中心官方月快照比對股票池 | `reports/survivorship/*.md`、`.json` |

---

## 7. 看懂輸出與報告

### 7.1 `status`

```
daily_prices_raw          7,412,300      ← 原始日線筆數
data_download_log          4,620
...
          dataset   status     n     rows
 TaiwanStockPrice  success  2160  7412300
 TaiwanStockPrice   failed     3        0   ← 重跑下載指令即會重試
```

`status` 欄位：`success` 成功、`empty` 確認沒有資料、`failed` 失敗（下次自動重試）、`gated` 需要付費方案。

### 7.2 資料品質報告（`reports/data_quality/`）

每筆問題有三個欄位要看：

| 欄位 | 值 | 意思 |
|---|---|---|
| 嚴重度 | `error` | 資料有錯，需要處理 |
| | `warning` | 可能有問題，需要注意 |
| | `info` | 已知原因的正常現象，只是記錄 |
| 建議處置 | `exclude` | 回測不應使用這筆資料 |
| | `flag` | 可以使用，但會被標記 |
| | `keep` | 正常，無需處理 |

常見類別：

| 類別 | 意思 | 需要處理嗎 |
|---|---|---|
| `no_trade` / `volume_without_price` | 當天無公告成交價（停牌或無人成交） | 否，系統已推斷為停牌期間 |
| `pre_listing:*` | 正式上市櫃前的興櫃 K 棒（開盤價是前日均價） | 否，已排除於股票池 |
| `pre_event_halt` | 減資、面額變更前的停止買賣 | 否 |
| `missing_trading_days` | 交易日沒有資料，原因不明 | 是，追查原因 |
| `unexplained_jump` | 漲跌超過漲跌停且沒有公司行動解釋 | 是，可能缺減資或資料錯誤 |
| `price_outside_limits` | 成交價超出計算出的漲跌停 | 是 |
| `vwap_outside_high_low` | 成交金額÷成交量落在當日高低之外 | 注意（原因調查中） |
| `ohlc_logic` 類（非 pre_listing） | 開高低收邏輯錯誤 | 是 |

**原則：系統不會刪除任何資料**，只會分類與標記。

### 7.3 倖存者偏誤報告（`reports/survivorship/`）

告訴你股票池是否漏掉歷史上存在過的股票。請特別閱讀「限制」段落：目前只能驗證上櫃股；**上市股未經官方比對，因此任何回測都不能宣稱已消除倖存者偏誤**。

### 7.4 日誌（`reports/logs/日期.log`）

每次執行的完整紀錄。出錯時先看這裡。

---

## 8. 查看資料庫裡的資料

資料庫檔案：`data/database/taiwan_stocks.sqlite`

### 8.1 用圖形介面

安裝免費的 **DB Browser for SQLite**（<https://sqlitebrowser.org/>），開啟上述檔案即可瀏覽與篩選。**請用唯讀模式開啟**，避免手動修改資料。

### 8.2 用 Python

```python
import sqlite3, pandas as pd
con = sqlite3.connect("data/database/taiwan_stocks.sqlite")

# 台積電原始價與還原價
pd.read_sql("""SELECT date, close, adj_close FROM daily_prices
               WHERE stock_id='2330' AND date >= '2024-01-01'""", con)

# 某天可交易的股票池（時點正確，含之後才下市的股票）
pd.read_sql("""SELECT stock_id, stock_name, exchange FROM security_master
               WHERE security_type='common' AND exchange IN ('TWSE','TPEX','UNKNOWN')
                 AND COALESCE(listing_date, first_price_date) <= '2018-01-02'
                 AND (delisting_date IS NULL OR delisting_date > '2018-01-02')""", con)

# 已完成的週 K（還原價）
pd.read_sql("""SELECT week_key, week_end, close FROM weekly_prices
               WHERE stock_id='2330' AND price_basis='adj' AND complete=1""", con)
```

### 8.3 主要資料表

| 資料表／檢視表 | 內容 |
|---|---|
| `security_master` | 股票基本資料：市場、產業、上市日（含判定依據）、下市日 |
| `security_history` | 股票在各市場的存在期間（含興櫃期間） |
| `daily_prices_raw` | **原始**日線（成交模擬只能用這個） |
| `daily_prices_adj` | **還原**日線（技術指標用） |
| `daily_prices` | 原始＋還原合併的檢視表 |
| `weekly_prices` | 週 K（`complete=1` 才是已完成的週） |
| `corporate_actions` | 公司行動；`dividend_history`、`stock_split_history`、`capital_reduction_history` 為其檢視表 |
| `price_limits` | 每日漲跌停價（`basis` 標示來源：自算或官方） |
| `trading_status`、`suspension_history` | 無價日與推斷的停牌期間 |
| `strategy_versions` | 策略參數快照（參數或公式一改就是新版本，舊版保留） |
| `technical_indicators` | 每檔每日指標值 |
| `strategy_signals`、`strategy_signals_readable` | 每檔每日每個條件是否成立、各版本是否進場；後者為可直接閱讀的檢視表 |
| `backtest_runs` | 每次回測的設定（策略版本、成本、期間、排除股票、資料版本） |
| `simulated_trades` | 每筆模擬交易：訊號日、進場日、進場價、初始停損、股數、出場日、出場價、出場原因、成本、股利、損益、R 倍數 |
| `portfolio_daily`、`portfolio_positions`、`portfolio_cash` | 每日總資產／現金／曝險／投組風險；每日持倉與停損價；現金流水 |
| `performance_metrics`、`data_quality_verdicts` | 各區段績效指標；每檔股票的 PASS／WARNING／FAIL |
| `trading_calendar` | 交易日曆 |
| `data_validation_log` | 每次資料品質檢查的問題 |
| `data_revisions` | 被資料商修正過的舊值 |
| `data_download_log` | 每次下載請求的結果（續傳依據） |
| `data_source_registry`、`data_versions` | 資料來源資訊、資料版本 |

---

## 9. 資料規則：使用前必須知道的事

1. **原始價 vs 還原價**：還原價讓除權息前後的價格連續，適合算技術指標；但**實際買賣價格、漲跌停、升降單位一律用原始價**。混用會讓回測失真。
2. **18:00 規則**：台北時間 18:00 前，系統不會存入當天的 K 棒（可能未定稿）。
3. **無價日**：當天沒有公告成交價時，開高低收為空值（不是 0），`has_price = 0`。
4. **興櫃期間**：部分股票上市櫃前在興櫃交易，那段 K 棒的開盤價是「前日均價」且沒有漲跌停。系統會判定正式上市日，興櫃期間不納入股票池（資料保留）。
5. **停牌是「推斷」的**：免費資料沒有官方停牌公告，系統由無價日推斷，標記為 `inferred`。
6. **漲跌停**：一般日由前一日收盤 ±10%（2015-06-01 前 ±7%）依升降單位計算；除權息、減資等事件日用官方值；新上市櫃前 5 個交易日無漲跌停。
7. **週 K**：每週結束日是該週實際最後交易日（週五休市就是週四）；當週尚未結束的 K 棒 `complete = 0`，不得用於歷史訊號。
8. **時點正確的股票池**：查詢某天能交易的股票時，系統只用當天已上市、尚未下市的股票，不會用今天的清單回推過去。

---

## 10. 疑難排解

| 現象 | 原因 | 處理 |
|---|---|---|
| 日誌 `server rate limit hit; pausing 600 s` | FinMind 額度用完（HTTP 402） | 不用處理，程式自動暫停 10 分鐘後繼續 |
| `Your level is free/register` | 該資料集需 FinMind 付費方案 | 系統記為 `gated` 並略過 |
| `Token is illegal` | `.env` 的 token 錯誤 | 到 FinMind 會員頁重新複製；若曾外流請「更新 Token」 |
| `trading_calendar is empty` | 還沒下載參考資料 | 先執行 `download reference` |
| 某些股票一直 `failed` | 網路或資料商問題 | 重跑下載指令；仍失敗請看日誌錯誤訊息 |
| 想重抓某檔 | — | `download prices --stocks 2330 --force` |
| `database is locked` | 同時執行兩個會寫入資料庫的指令 | 一次只執行一個指令 |
| 測試沒有全部通過 | 環境或版本不符 | 確認 Python ≥ 3.11、已 `pip install -r requirements.txt` |
| 想換資料庫位置 | — | 在 `.env` 加 `TQS_DB_PATH=路徑` |

---

## 11. 已知限制

完整說明見 [`docs/stage2_report.md`](stage2_report.md) §6。重點：

- **倖存者偏誤未完全排除**：上市股無法以證交所官方資料比對；部分下市股市場別未知。
- **免費資料沒有**：處置股、全額交割股、歷史產業分類（目前用現在的分類回填並標記）。
- **無成交日之後的參考價**可能與官方不同（只影響成交稀少的股票）。
- **推斷的上市日**可能比實際早 1–3 週（不影響交易，因策略需長暖機期）。
- FinMind 免費額度：文件寫 600 次／小時，實際可能更少；全市場首次下載需要一整個晚上。

---

## 12. 名詞解釋

| 名詞 | 意思 |
|---|---|
| 還原價（Adjusted Price） | 扣除除權息、分割等影響後的連續價格序列 |
| 倖存者偏誤（Survivorship Bias） | 只用現在還存在的股票回測，忽略已下市的失敗公司，會高估績效 |
| 前視偏誤（Look-ahead Bias） | 回測時用了「當時還不知道」的資料 |
| as-of 日期 | 資料截止日；系統保證不使用此日之後的資料 |
| 興櫃 | 正式上市櫃前的交易市場；議價交易、沒有開盤價、沒有漲跌停 |
| 增量更新 | 只下載還沒有的資料 |
| 續傳 | 中斷後從斷點繼續，不重來 |
| 資料版本 | 某一時刻資料庫內容的指紋，讓回測結果可以追溯 |
| 推斷（inferred） | 由資料推論而非官方公告，可信度較低 |
