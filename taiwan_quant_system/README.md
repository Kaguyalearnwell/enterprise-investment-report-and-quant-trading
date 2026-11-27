# taiwan_quant_system

台股全市場量化交易系統：Fibonacci MA + Bollinger Bandwidth Breakout + Weekly Trend Filter。

**狀態：Phase 1（數學定義與邏輯審核）完成。程式原型，尚未以真實資料回測。**
規格：`../prompts/03_taiwan_full_market_system.md`；Phase 1 文件：`../docs/phase1_math_spec.md`。

```bash
pip install -r requirements.txt
python3 -m pytest -q
```

已完成：`config/default.toml`、`src/indicators.py`、`src/weekly_filter.py`、`src/signal_generator.py`、`tests/`。
其他模組（data_loader、execution_engine、portfolio、backtester、performance、visualization、tradingview/strategy.pine、main.py）依 Phase 2–7 開發。
