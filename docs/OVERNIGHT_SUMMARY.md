# 夜間自主作業總整理（2026-11-28 台北時間 01:24 起）

> 你睡前授權我「一路進行下去」。這份文件讓你不用翻對話紀錄就能檢查我做了什麼。
> 所有程式都已 commit 並 push 到 `claude/trusting-gauss-pam41z`；**186 項 Python 測試全部通過**，前端 TypeScript 型別檢查與建置通過。

---

## 1. 一句話結論

**統計驗證的早期訊號對策略不利**：在 72 檔開發樣本上，完整策略 E 在 In-Sample 有些微優勢，但到了 Validation 就消失，並出現過度擬合的典型特徵（Deflated Sharpe 0.16、樣本內越好的參數樣本外越差）。**這不是正式結論**（樣本不是全市場、研究預設值未確認、OOS 尚未解封），但值得你在正式評估前先知道。

---

## 2. 完成的工作

| # | 工作 | 文件 | 重點 |
|---|---|---|---|
| 1 | 你要的 Stage 5 Prompt | `prompts/05_stage5_statistical_validation.md` | 可直接交給 Claude Code 執行 |
| 2 | 預先登記（在任何實驗前 commit） | `docs/stage5_preregistration.md` | OOS 門檻全部取自你的 Prompt 02／03／04；參數網格 252 組事先固定 |
| 3 | Stage 5 統計驗證 | `docs/stage5_report.md`、`reports/stage5_results.md` | 2×2、2×2×2、A→E、Leave-One-Out、200 次隨機基準、條件重複性、Bootstrap、Monte Carlo、參數高原、Walk-Forward、PBO、Deflated Sharpe、市場環境、Holm |
| 4 | Stage 6.1 基本面資料驗證 | `docs/stage6_1_fundamental_data.md` | FinMind 財報三表、月營收、股利政策、PER 實測 |
| 5 | Stage 6.2 基本面資料管線 | （程式）`download fundamentals` | 每筆資料記錄「最早可用日」，回測不會用到尚未公布的財報 |
| 6 | Stage 7.1 財務指標 | `backend/research/financial_metrics.py` | 時點正確的 TTM 指標；以台積電公布的 2024 年數字驗證 |
| 7 | Stage 7.2 估值工具 | `backend/valuation/models.py` | DCF、反向 DCF、情境加權、安全邊際、歷史百分位（只有工具，不替任何公司估值） |
| 8 | Dashboard 唯讀 API | `backend/api/app.py` | 只有 GET；OOS 封存延伸到 API（曲線、交易、指標、持倉） |
| 9 | Dashboard 前端第一版 | `frontend/`、`docs/USER_GUIDE.md` §6.6 | 總覽、每日訊號（匯出 CSV／Excel）、個股圖與條件解釋、基本面、回測、統計驗證、資料品質；已在開發資料庫上實際開啟並截圖檢查（含深色模式、手機寬度） |

---

## 3. 重要發現（依重要性）

### 3.1 策略（Stage 5，72 檔開發樣本）

1. **In-Sample 優勢在 Validation 消失**：E 在 IS 勝過約 95% 的隨機進場；在 Validation 只在第 54 百分位（等同隨機）。
2. **過度擬合警訊**：Deflated Sharpe 0.16（門檻 0.95）；PBO 0.475（勉強低於 0.5）；IS→OOS Sharpe 斜率 −1.39；依高原規則選的參數在 Validation 比你的原始參數更差；Walk-Forward 7 個測試年中 5 年虧損。
3. **週線濾網是唯一一致有效的模組**（IS 與 Validation 都提高期望值、降低回撤）。
4. **均線濾網在其他條件存在時沒有額外貢獻**；**成交量確認與突破高度重疊**（突破日 67% 同時爆量）。
5. **成本會吃掉 IS 的優勢**（壓力成本下期望值轉負）。
6. **四個預先登記假設經 Holm 校正後都不顯著。**

### 3.2 程式問題（都已修正並加回歸測試）

| 問題 | 影響 | 修正 |
|---|---|---|
| 同日多檔訊號的隨機排序受「訊號讀入順序」影響 | 同樣資料不同路徑挑到不同股票，違反可重現性 | 先依代號固定順序（`backtest/3`）；Stage 4 報告已重跑更新 |
| Bootstrap 與指標表的區段報酬算法不一致 | Validation Sharpe 顯示不同 | 統一 |
| API：跨越封存點的「年度」指標（2024 年）含有 OOS 日 | 封存時仍可間接看到 OOS 績效 | 只顯示整年在封存點以前的年度 |
| API：封存點前進場、封存點後出場的交易會顯示出場價與損益 | 洩漏封存期間價格結果 | 這類交易的出場資訊一律隱藏 |

### 3.3 資料（Stage 6）

1. **財報沒有公告日期**；公開資訊觀測站在雲端被封鎖 → 用法定申報期限推定可用日。
2. **月營收實際收錄常晚於法定的 10 日**（36 筆中 14 筆晚於 10 日、最晚 20 日；金控每月 13–17 日）→ 歷史資料改用「次月 20 日後」。
3. **現金流量表是年初至今累計值**，損益表是單季值（FinMind 文件沒寫）→ 已換算並以台積電 2024 年營收 2,894.3 十億、營業現金流 1,826.2 十億驗證。
4. **股利政策表的 `date` 有未來日期** → 改用公告日判斷可用時點。
5. **下市股的月營收幾乎沒有**（基本面資料的存活者偏誤）。

---

## 4. 需要你決定的事（依優先順序）

| # | 決定 | 選項／我的建議 | 影響 |
|---|---|---|---|
| 1 | **研究預設值確認**（追價 1 ATR、投組風險計算、同日排序、零股規則、市場環境分類） | 見 `docs/stage4_report.md` §5 | 正式回測與 OOS 解封的前提 |
| 2 | **看到 Stage 5 警訊後，要不要先調整策略**（例如移除沒有貢獻的均線或成交量條件） | 若要調整：必須建立**新策略版本 + 新預先登記**，且不得用 OOS 挑選 | 決定接下來研究方向 |
| 3 | **全市場資料庫**（你的電腦） | 依 `docs/INSTALL.md`；股價約 8 小時、基本面約 19 小時，可分晚執行 | 所有正式結論的前提 |
| 4 | 月營收可用日規則 | 建議「次月 20 日後」（實測上限） | 基本面研究的前視偏誤 |
| 5 | **企業評級的打分標準**（例如毛利率多少算幾分） | 你的 Prompt 04 只有權重沒有門檻。選項：(a) 你提供門檻；(b) 用同產業百分位打分（我建議）；(c) 由研究流程（Prompt 01）逐家給分並記錄依據 | Stage 7.3 才能開始 |
| 6 | 處置股、全額交割股 | 付費資料或在你電腦抓官方公告 | 撮合限制完整性 |

---

## 5. 我刻意沒有做的事

- **沒有解封 OOS**：研究預設值未確認前，OOS 只能看一次，不應浪費在未確認的版本上。
- **沒有建立企業評級分數**：打分門檻沒有定義（第 4 節 #5），自行訂定等於把我的猜測當成你的標準。
- **沒有調整策略參數**：即使 Stage 5 顯示某些條件沒有貢獻，改策略是你的決定。
- **沒有替任何公司做估值**：只建立工具。

---

## 6. 本次 commit 清單（時間順序）

```
5d53a20 Stage 5: prompt and pre-registration (committed before any Validation/OOS look)
5b53f4a Stage 5: schema v6 experiment registry, experiment context, statistics toolkit
1e130db Stage 5: experiment context tests
b8f456b Stage 5 pre-registration appendix: fixed parameter grid (before running)
1b40d59 Stage 5: designs and robustness with tests
68d53c2 Engine: same-day ranking independent of signal input order (backtest/3)
bd7e1cc Stage 4 report: numbers regenerated with backtest/3
dcaef10 Stage 6.1: fundamental data verification
b133bd7 Stage 5: full run results (IS + Validation; OOS sealed)
500e3ea Stage 5 report, docs
53126e9 Stage 6.2: fundamentals pipeline (schema v7 ...)
3b163a9 Stage 7.1: point-in-time financial metrics ...
9488b3e Stage 7.2 valuation tools、本文件
5a03e24 Dashboard API: read-only FastAPI with OOS seal
6e0db6b Dashboard frontend (React+TS+Vite+ECharts)
（之後）文件更新
```

---

## 7. 建議你起床後先看

1. 本文件 §4 的決定事項。
2. `docs/stage5_report.md` §2（策略的早期警訊）。
3. 若有時間：`reports/stage5_results.md`（完整數字）。
