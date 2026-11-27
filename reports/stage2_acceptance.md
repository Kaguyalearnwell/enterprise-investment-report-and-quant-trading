# Stage 2 驗收結果（真實資料，自動產生）

- 資料截止日：2026-11-26；資料版本：1；資料品質 run：`dq-2026-11-27T07:00:12+00:00`
- 測試股票 14 檔（刻意挑選困難案例）；資料庫：223,716 筆原始日線（含先前測試下載的股票）

## 驗收清單

| 項目 | 結果 | 依據 |
|---|---|---|
| 能取得上市股票清單 | ✅ | security_master TWSE rows |
| 能取得上櫃股票清單 | ✅ | security_master TPEX rows |
| 能取得可用的下市股票資料 | ✅ | 3068/6022/1333 have prices up to delisting |
| 能下載指定股票的歷史日線 | ✅ | prices: 3 ok, 11 already covered |
| 能批次下載股票資料 | ✅ | 14 stocks in one command |
| 能中斷後續傳 | ✅ | SIGKILL test 2026-11-27: 43 -> 69 stocks, 0 duplicates, re-run 0 requests (docs/stage2_report.md) |
| 能將資料儲存至 SQLite | ✅ | 223,716 raw daily rows |
| 能正確區分原始價格與還原價格 | ✅ | check_adjusted: 0 errors |
| 能處理公司行動資料 | ✅ | 244 corporate actions |
| 能取得或建立交易日曆 | ✅ | 3393 trading days 2013..2026-11-26, none after as-of |
| 能建立已完成週 K | ✅ | 94,598 weekly rows; rebuild check 0 errors |
| 能產生資料品質報告 | ✅ | reports/data_quality/dq-2026-11-27T070012+0000.md |
| 能執行 OHLC 檢查 | ✅ | ohlc errors: 177 |
| 能執行重複資料檢查 | ✅ | duplicate errors: 0 |
| 能執行前視偏誤檢查 | ✅ | backend/tests/test_no_lookahead.py (perturbation + power test), as-of cuts |
| 能產生倖存者偏誤限制報告 | ✅ | survivorship-2026-11-27T064809+0000.json |

## 覆蓋範圍（每檔）

| 代號 | 名稱 | 市場 | 測試目的 | 筆數 | 期間 | 無價日 | 公司行動 | 推斷停牌段 | 週K | 下市日 | 來源 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1101 | 台泥 | TWSE | large cap | 3393 | 2013-01-02..2026-11-26 | 3 | 18 | 2 | 717 | nan | finmind |
| 1333 | 恩得利工業股份有限公司 | UNKNOWN | long suspension before delisting | 1767 | 2013-01-02..2020-04-06 | 120 | 1 | 85 | 373 | 2020-11-17 | finmind |
| 2317 | 鴻海 | TWSE | no-price day 2025-07-30 | 3387 | 2013-01-02..2026-11-26 | 2 | 18 | 3 | 717 | nan | finmind |
| 2327 | 國巨* | TWSE | capital reductions 2013-2017 | 3344 | 2013-01-02..2026-11-26 | 4 | 22 | 10 | 711 | nan | finmind |
| 2330 | 台積電 | TWSE | large cap, quarterly cash dividends | 3393 | 2013-01-02..2026-11-26 | 0 | 39 | 0 | 717 | nan | finmind |
| 2881 | 富邦金 | TWSE | financial | 3393 | 2013-01-02..2026-11-26 | 0 | 22 | 0 | 717 | nan | finmind |
| 3068 | 美磊科技股份有限公司 | TPEX | delisted 2018 | 1330 | 2013-01-02..2018-06-06 | 1 | 8 | 1 | 281 | 2018-06-13 | finmind+tpex_snapshot |
| 5326 | nan | UNKNOWN | merger, in no vendor list | 420 | 2013-01-02..2014-09-18 | 0 | 1 | 0 | 89 | nan | downloader:auto |
| 5347 | 世界 | TPEX | TPEx | 3393 | 2013-01-02..2026-11-26 | 1 | 18 | 1 | 717 | nan | finmind |
| 6022 | 大眾綜合證券股份有限公司 | TPEX | delisted 2017 | 1136 | 2013-01-02..2017-08-21 | 0 | 5 | 0 | 240 | 2017-08-28 | finmind+tpex_snapshot |
| 6488 | 環球晶 | TPEX | TPEx, listed 2014 | 2947 | 2014-10-28..2026-11-26 | 2 | 17 | 2 | 623 | nan | finmind |
| 6531 | 愛普* | TWSE | par value change 2021-10-18 | 2789 | 2015-06-12..2026-11-26 | 3 | 12 | 4 | 591 | nan | finmind |
| 8070 | 長華* | TWSE | par value change 2020-08-17 | 3376 | 2013-01-02..2026-11-26 | 1 | 28 | 3 | 715 | nan | finmind |
| 9929 | 秋雨 | TWSE | volume without price | 3378 | 2013-01-02..2026-11-26 | 91 | 3 | 73 | 715 | nan | finmind |

## 資料品質摘要

嚴重度：{'error': 177, 'info': 255, 'warning': 162}

| 檢查 | 類別 | 嚴重度 | 筆數 |
|---|---|---|---|
| calendar | missing_trading_days | warning | 13 |
| no_price_day | no_trade | info | 68 |
| no_price_day | volume_without_price | info | 160 |
| ohlc_logic | high_below_open_close_or_low | error | 86 |
| ohlc_logic | low_above_open_close | error | 89 |
| ohlc_logic | nonpositive_price | error | 2 |
| price_limits | price_outside_limits | warning | 23 |
| returns | unexplained_jump | warning | 7 |
| volume | volume_spike | info | 27 |
| volume | vwap_outside_high_low | warning | 119 |

完整清單：`dq-2026-11-27T070012+0000.md`

## 漲跌停：自算 vs 櫃買中心官方「次日漲跌停價」

- 比對 1421 筆（15 檔、167 個日期），一致 1412 筆（99.4%）

| 快照日 | 次日 | 代號 | 自算方式 | 官方 | 自算 |
|---|---|---|---|---|---|
| 2015-10-01 | 2015-10-02 | 6488 | computed | 0.01..9995.0 | 60.9..74.3 |
| 2017-07-03 | 2017-07-04 | 1264 | computed | 203.0..247.5 | 232.5..283.5 |
| 2022-06-01 | 2022-06-02 | 1259 | computed | 61.0..74.4 | 61.6..75.2 |
| 2022-08-01 | 2022-08-02 | 1336 | computed | 18.4..22.4 | 18.75..22.85 |
| 2024-10-01 | 2024-10-04 | 1240 | computed | 47.8..58.4 | 47.65..58.1 |
| 2024-10-01 | 2024-10-04 | 1294 | computed | 0.01..9995.0 | 107.0..130.0 |
| 2025-06-02 | 2025-06-03 | 1295 | computed | 0.01..9995.0 | 78.2..95.4 |
| 2026-01-02 | 2026-01-05 | 1259 | computed | 55.2..67.4 | 56.0..68.4 |
| 2026-09-01 | 2026-09-02 | 1240 | computed | 49.75..60.7 | 52.2..63.8 |

## 倖存者偏誤稽核（櫃買中心月快照）

- 快照 167 份；看到上櫃普通股 1021 檔
- 補入股票池 0 檔：
- 下市股判定為上櫃 41 檔；仍未知 431 檔
- 補入前最低月覆蓋率 100.00%
- **上市（TWSE）未稽核；不能宣稱已消除倖存者偏誤。**
