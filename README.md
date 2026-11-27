# enterprise-investment-report-and-quant-trading

機構級投資研究與量化交易研究框架。

## 內容

| 路徑 | 說明 |
|---|---|
| [`prompts/01_buyside_equity_research.md`](prompts/01_buyside_equity_research.md) | 買方機構級個股研究報告 Prompt（市場預期差、財報品質、估值交叉驗證、多空辯論、投資決策量化） |
| [`prompts/02_quant_strategy_research.md`](prompts/02_quant_strategy_research.md) | 量化策略研究 Prompt：Fibonacci MA 13/34/55 + Bollinger Bandwidth 壓縮突破 + 多週期確認 |
| [`docs/stage1_strategy_research.md`](docs/stage1_strategy_research.md) | **Stage 1 成果**：策略邏輯與數學審核（Prompt 02 第 1–9 部分） |
| [`prompts/03_taiwan_full_market_system.md`](prompts/03_taiwan_full_market_system.md) | 台股全市場系統開發主規格（已確認參數、Phase 1–8 流程） |
| [`docs/phase1_math_spec.md`](docs/phase1_math_spec.md) | **Phase 1 成果**：數學定義、時序表、邏輯審核、測試結果 |
| [`docs/stage2_1_data_sources.md`](docs/stage2_1_data_sources.md) | **Stage 2.1 成果**：FinMind／TWSE／TPEx 資料來源實測驗證 |
| [`docs/stage2_2_database.md`](docs/stage2_2_database.md) | **Stage 2.2 成果**：SQLite schema、遷移、讀寫介面與測試 |
| [`taiwan_quant_system/`](taiwan_quant_system/) | Python 系統（目前：指標、週線濾網、訊號、23 項因果性測試） |

## 兩階段研究流程

1. **Stage 1 — 策略研究與數學審核**（已完成）：交易邏輯、公式、前視偏誤檢查、進出場規則、消融實驗設計、回測架構。
2. **Stage 2 — 程式開發與實證回測**（待確認後進行）：Python 專案、歷史回測、樣本外分析、壓力測試、實盤可行性評估。

核心研究問題：**獲利究竟來自 13/34/55 均線趨勢、布林帶寬壓縮突破，還是兩者結合產生的額外 Alpha？**
