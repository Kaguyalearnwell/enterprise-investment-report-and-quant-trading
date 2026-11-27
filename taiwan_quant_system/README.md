# taiwan_quant_system

台股全市場量化交易系統：Fibonacci MA + Bollinger Bandwidth Breakout + Weekly Trend Filter。

**狀態：Phase 1 完成；Stage 2.1（資料來源確認）、Stage 2.2（資料庫）完成。資料庫尚無真實市場資料，尚未回測。**
規格：`../prompts/03_taiwan_full_market_system.md`；Phase 1 文件：`../docs/phase1_math_spec.md`。

```bash
pip install -r requirements.txt
python3 -m pytest -q
```

已完成：`config/default.toml`、`config/settings.py`、`config/data_sources.py`、`src/indicators.py`、`src/weekly_filter.py`、`src/signal_generator.py`、`src/database/`（schema、migrations、db_manager）、`scripts/verify_data_sources.py`、`tests/`（45 項）。

設定：複製 `.env.example` 為 `.env`。Token 只從環境變數 `FINMIND_TOKEN` 讀取。
其他模組（data_loader、execution_engine、portfolio、backtester、performance、visualization、tradingview/strategy.pine、main.py）依 Phase 2–7 開發。
