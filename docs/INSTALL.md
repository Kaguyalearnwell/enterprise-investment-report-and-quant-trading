# 安裝與資料建置手冊（在你的電腦上執行）

> 本系統只做研究、分析與歷史模擬，**不進行任何真實交易**。
> 目前完成到 Stage 4（回測與投資組合模擬）。Dashboard 在 Stage 9 才會完成。

## 1. 需要的東西

| 項目 | 版本 | 說明 |
|---|---|---|
| Python | 3.11 以上（開發環境為 3.13） | <https://www.python.org/downloads/> |
| Git | 任意新版 | 下載程式碼 |
| FinMind 帳號 | 免費註冊並驗證信箱 | 會員頁取得 API token（額度 600 次／小時） |
| 硬碟空間 | 約 2 GB | 全市場資料庫約 0.85 GB，加上原始回應壓縮檔 |

## 2. 安裝

```bash
git clone https://github.com/kaguyalearnwell/enterprise-investment-report-and-quant-trading.git
cd enterprise-investment-report-and-quant-trading
git checkout claude/trusting-gauss-pam41z      # 目前的開發分支
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS / Linux:  source .venv/bin/activate
pip install -r requirements.txt
python -m pytest                              # 應顯示全部測試通過
```

## 3. 設定 Token

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

用記事本打開 `.env`，把 `FINMIND_TOKEN=proxy-injected` 改成你的 token。**`.env` 已設定不會上傳到 git，請不要把 token 貼到程式碼、聊天或任何公開地方。**

## 4. 建立資料庫（第一次，約 8 小時，可中斷）

以下指令都在專案根目錄執行。**任何一步中斷（關機、斷線、Ctrl-C）都沒關係，重跑同一個指令會從中斷處繼續。**

```bash
python -m backend.cli init-db                    # 建立資料庫（data/database/taiwan_stocks.sqlite）
python -m backend.cli registry                   # 登錄資料來源資訊
python -m backend.cli download reference         # 股票清單、下市表、交易日曆、分割、報酬指數（約 10 次請求）
python -m backend.cli audit-survivorship         # 櫃買中心官方月快照比對，補齊漏列股票（約 10 分鐘，不耗 FinMind 額度）
python -m backend.cli download prices --all      # 全市場日線（約 2,300 次請求）
python -m backend.cli download dividends --all   # 全市場除權息（約 2,300 次請求）
python -m backend.cli process --all              # 還原價、漲跌停、停牌推斷、週線
python -m backend.cli validate --all             # 資料品質報告 -> reports/data_quality/
python -m backend.cli signals compute --all      # 指標與策略訊號（約 15 分鐘）
python -m backend.cli version "initial full load"
python -m backend.cli status                     # 查看各資料表筆數與下載狀態
```

建議先用 10 檔測試，確認一切正常再跑全市場：

```bash
python -m backend.cli download prices --stocks 2330 2317 3068 6022 1333 5326 6488 5347 2881 1101
python -m backend.cli download dividends --stocks 2330 2317 3068 6022 1333 5326 6488 5347 2881 1101
python -m backend.cli process --stocks 2330 2317 3068 6022 1333 5326 6488 5347 2881 1101
python -m backend.cli validate --stocks 2330 2317 3068 6022 1333 5326 6488 5347 2881 1101
```

## 5. 每日更新（約 4 小時，建議每天台北時間 18:00 之後）

```bash
python -m backend.cli download reference
python -m backend.cli download prices --all
python -m backend.cli download dividends --all
python -m backend.cli process --all
python -m backend.cli validate --all
python -m backend.cli signals compute --all
python -m backend.cli signals list
python -m backend.cli version "daily update"
```

- 台北時間 18:00 前執行時，系統只會抓到「前一個交易日」為止（FinMind 17:30 才更新當日資料，避免存到未定稿的 K 棒）。
- 每次更新會重新下載最後 7 天，若資料商修正了數字，舊值會保存在 `data_revisions`。

**排程**：Windows 用「工作排程器」，macOS / Linux 用 `cron`，每個交易日 18:30 執行上面的指令。

## 6. 常見問題

| 狀況 | 說明 |
|---|---|
| 日誌出現 `server rate limit hit; pausing 600 s` | FinMind 回傳 402（額度用完），程式會自動暫停 10 分鐘後繼續，不需處理 |
| `Your level is free/register` | 該資料集需要 FinMind 付費方案；系統會記錄為 `gated` 並略過 |
| 想重新下載某檔 | 加 `--force`，例如 `download prices --stocks 2330 --force` |
| 下載失敗的股票 | `status` 指令會顯示 `failed`；直接重跑同一個下載指令就會重試 |
| 資料庫位置 | `data/database/taiwan_stocks.sqlite`；可在 `.env` 用 `TQS_DB_PATH` 改位置 |

## 7. 目前已知的資料限制

詳見 `docs/stage2_1_data_sources.md` 與每次產生的 `reports/survivorship/*.md`。重點：上市（TWSE）股票未經官方比對、處置股與全額交割股在免費資料中沒有、歷史產業分類只有目前版本。**回測結果不能宣稱已完全排除倖存者偏誤。**
