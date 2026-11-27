# Prompt 03：台股全市場量化交易系統開發

**Fibonacci MA + Bollinger Bandwidth Breakout + Weekly Trend Filter**

> 本 Prompt 為 `taiwan_quant_system/` 的主規格。Prompt 02 / Stage 1 的研究設計仍適用，本份提供已確認的系統參數。
> 執行狀態見 [`docs/phase1_math_spec.md`](../docs/phase1_math_spec.md)。

## 一、角色設定（ROLE）

你是一位頂尖避險基金的資深量化研究員、系統交易工程師及投資組合經理。精通 Quantitative Trading、Technical Analysis、Time Series Analysis、Statistical Testing、Portfolio Optimization、Market Microstructure、Python Algorithmic Trading、TradingView Pine Script v6、Backtesting & Walk-Forward Analysis。

任務：將以下交易構想開發成具備統計驗證能力、可重複執行、可實際回測及部署的系統化交易策略。必須保持客觀，不得預設策略具有獲利能力。

核心目標：
1. 找出策略是否具有正期望值。
2. 確認優勢在扣除交易成本後是否持續存在。
3. 防止前視偏誤、倖存者偏誤及過度擬合。
4. 建立完整的資金管理與風險控制機制。
5. 建立 Python 回測系統及 TradingView 訊號監控系統。
6. 評估策略是否具備實際部署條件。

## 二、交易系統基本設定

| 項目 | 設定 |
|---|---|
| 交易市場 | 台灣證券交易所上市股票＋櫃買中心上櫃股票 |
| 股票範圍 | 全市場普通股 |
| 交易方向 | 只做多 |
| 主要交易週期 | 日線 |
| 趨勢確認週期 | 週線 |
| 進場邏輯 | 布林上軌突破＋成交量確認 |
| 趨勢判斷 | MA13 / MA34 / MA55 |
| 波動率判斷 | Bollinger Bandwidth |
| 停損方式 | ATR 動態停損 |
| 移動停利 | ATR Trailing Stop |
| 初始本金 | NT$1,000,000 |
| 單筆風險 | 1%（NT$10,000） |
| 單一股票市值上限 | 10% |
| 同產業市值上限 | 25% |
| 最大總投入資金 | 90% |
| 投組初始風險上限 | 5% |
| 開發語言 | Python |
| 圖表與警示 | TradingView Pine Script v6 |

所有參數均為初始研究設定，必須經過歷史回測及樣本外驗證，不得直接視為最佳參數。

## 三、核心策略邏輯

策略名稱：Fibonacci Trend Following + Bollinger Squeeze Breakout + Weekly Regime Filter

流程：(1) 週線確認主要趨勢 → (2) 日線 MA13/34/55 多頭排列 → (3) 布林帶寬處於歷史低波動 → (4) 價格突破布林上軌 → (5) 成交量明顯放大 → (6) 通過流動性條件 → (7) 下一交易日開盤買進 → (8) ATR 計算停損及部位 → (9) 趨勢破壞、ATR 移動停利及時間停損出場。

不得將所有條件視為理所當然有效，必須透過消融實驗驗證每個模組的實際貢獻。

## 四、趨勢辨識模組

```
MA13 = SMA(Close, 13);  MA34 = SMA(Close, 34);  MA55 = SMA(Close, 55)
Slope55 = (MA55[t] - MA55[t-5]) / 5
DailyTrend = MA13 > MA34 > MA55 AND Slope55 > 0
```

週線使用相同邏輯，但必須只使用已完成的上一週週 K。研究 MA13–MA34 距離、MA34–MA55 距離、斜率、排列持續時間、糾結程度；評估是否需要額外的趨勢強度濾網。不得在未經測試前增加不必要的條件。

## 五、波動率壓縮模組

```
MB = SMA(Close, 20);  UB = MB + 2·STD(Close, 20);  LB = MB − 2·STD(Close, 20)
BBW = (UB − LB) / MB
BBW Percentile：過去 120 個交易日
Squeeze：BBW Percentile <= 20%
RecentSqueeze：最近 6 個交易日內至少一次 Squeeze
```

研究不同壓縮百分位的突破品質、壓縮時間長短、壓縮後趨勢延續機率、MFE、MAE；比較固定百分位與動態百分位方法。

## 六、突破進場模組

```
Close[t] > UpperBand[t]
Volume[t] > 1.5 × SMA(Volume, 20)[t]
SMA(Close × Volume, 20) >= NT$20,000,000

ENTRY[t] = DailyTrend AND WeeklyTrend AND RecentSqueeze AND Close > UB
           AND Volume > 1.5 × VolumeMA20 AND AverageTurnover20 >= 20,000,000
```

時序：t 日收盤確認、t+1 日開盤執行；漲停鎖死無法成交不得假設買進；跳空過高須依實際成交價重新計算部位；超過風險或追價限制則取消。

## 七、跨週期濾網

```
Weekly MA13 > Weekly MA34 > Weekly MA55
Weekly MA55 > Previous Weekly MA55
```

規範：只用已完成週 K；不得用未收盤本週資料；避免 Repainting；Python 與 TradingView 週線對齊一致；建立跨週期資料測試；週五休市、連假及資料缺漏須以實際交易日曆處理。研究目的：能否降低日線假突破並改善風險調整後報酬。

## 八、資金管理與倉位控制

```
ATR = Wilder ATR(14)
StopLoss = EntryPrice − 2.5 × ATR(14)
Shares = RiskBudget / (EntryPrice − StopLoss)
```

需考慮：手續費、證交稅、滑價、跳空風險、可用現金、單一股票權重、產業集中度、投組整體風險。股數取整並遵守交易單位。風控：單一股票 ≤ 10%、單一產業 ≤ 25%、總投入 ≤ 90%、投組初始停損風險 ≤ 5%。另測試不同單筆風險比例對長期複利及 MDD 的影響。

## 九、出場策略

- A. 趨勢破壞：收盤連續兩日低於 MA34，下一交易日開盤出場。
- B. ATR 移動停利：`TrailingStop = HighestCloseSinceEntry − 3 × ATR(14)`，只上移不下移。
- C. 時間停損：持有 40 日仍未曾達到 1R 最大浮盈，下一交易日開盤退出。

記錄每筆出場原因；另比較 MA13、MA34、布林中軌跌破、ATR Trailing、固定 R 倍數停利、Time Stop。

## 十、台股市場微結構及交易成本

納入：買賣手續費、券商折扣、賣出證交稅、滑價、最小交易單位、漲跌停、停牌與下市、除權息及分割、流動性限制。

| 情境 | 手續費 | 滑價 |
|---|---|---|
| 樂觀 | 3 折 | 3 bps |
| 基準 | 6 折 | 5 bps |
| 壓力 | 原價 | 15 bps |

標示費率資料日期；不可用當沖稅率套用波段交易。須處理開盤跳空、漲停買不到、跌停賣不出、停牌、成交量不足、最低手續費、部分成交。

## 十一、消融實驗

A：MA；B：MA + BBW 壓縮；C：+ 突破；D：+ 成交量；E：+ 週線濾網。比較 CAGR、Sharpe、Sortino、MDD、PF、勝率、平均報酬、持有期、換手率、交易次數。核心問題：週線濾網是否真正改善？成交量是否減少假突破？壓縮是否改善突破後報酬分布？無樣本外增量者應移除。

## 十二、回測與統計驗證

資料來源：證交所、櫃買中心、公開資訊觀測站、可靠歷史行情供應商。研究範圍至少 2015–2026。須使用歷史時點股票池，納入下市櫃股票。切分 IS 60% / Validation 20% / OOS 20%，時間序列切分。另做 Walk-Forward、Rolling、Expanding、Bootstrap、Monte Carlo、參數敏感度、多重假設檢定控制。

| 參數 | 範圍 |
|---|---|
| MA 快線 | 10–16 |
| MA 中線 | 28–40 |
| MA 慢線 | 45–65 |
| BB Period | 15–30 |
| BB Std | 1.5–2.5 |
| BBW Percentile | 5–30 |
| 壓縮回看期間 | 3–10 |
| 成交量倍數 | 1.2–2.0 |
| ATR 停損倍數 | 2.0–3.0 |
| ATR 移動停利倍數 | 2.0–4.0 |

不得只回報最佳參數，必須分析參數高原及樣本外穩健性。

## 十三、績效指標

報酬（CAGR、Total、Annual）、風險（Vol、Sharpe、Sortino、Calmar、MDD、MDD Duration）、交易（Win Rate、Payoff、PF、Expectancy、持有期、換手率）。基準：加權指數、0050、Buy & Hold、單純 MA13/34/55。參考標準：OOS Sharpe > 1、PF > 1.3、成本後正期望值、OOS CAGR > 0、MDD 符合限制、不依賴單一參數或單一年度。這些是研究門檻，不是獲利保證。

## 十四、Python 系統

```
taiwan_quant_system/
  config/  data/
  src/ data_loader.py data_cleaner.py indicators.py weekly_filter.py signal_generator.py
       position_sizing.py execution_engine.py portfolio.py backtester.py performance.py visualization.py
  tradingview/strategy.pine
  tests/  reports/  main.py
```

功能：全市場資料、股票池、清洗、指標、週線、訊號、部位、成本、撮合、投組、績效、圖表。每筆交易保存：代號、名稱、訊號日、進場日、進場價、初始停損、股數、投入資金、出場日、出場價、出場原因、交易成本、損益、R Multiple。

## 十五、TradingView 整合（Pine Script v6）

MA、布林、BBW Percentile、Squeeze、成交量確認、週線濾網、進場訊號、ATR 停損、移動停利、Alert。`request.security()` 避免未確認週線資料並明確設定 lookahead；訊號只在日 K 收盤確認；不得 Repainting；Python 與 TradingView 訊號可交叉驗證。TradingView 負責監控與警示；Python 負責回測、部位、投組與正式績效。

## 十六、進階研究（基礎策略通過驗證後）

- A. False Breakout Detection：實體比例、上影線、量能、隔日延續、MA13–MA34 距離。
- B. Relative Strength Ranking：相對加權指數 20/60/120 日報酬，用於多檔同時訊號排序。
- C. Market Regime Detection：多頭／空頭／盤整／高波動，是否動態調整總曝險。

所有進階功能須透過消融實驗證明增量價值。

## 十七、最終研究報告

經濟直覺、數學定義、進出場、資金管理、成本模型、消融、IS、OOS、WFA、參數穩健性、Regime、MDD、交易紀錄、淨值曲線、回撤曲線、每年績效、TradingView 訊號驗證、失效條件。

最終判斷：A. 值得繼續研究；B. 回測有效但未達實盤；C. 具備模擬交易條件；D. 具備小額實盤測試條件；E. 缺乏統計優勢，建議放棄。不得只因歷史報酬高就認定有效。

## 十八、執行流程

Phase 1 數學定義與邏輯審核 → Phase 2 資料庫及全市場股票池 → Phase 3 基礎策略回測 → Phase 4 成本及資金管理 → Phase 5 消融與參數穩健性 → Phase 6 OOS 及 Walk-Forward → Phase 7 TradingView → Phase 8 模擬交易及實盤評估。

每階段：說明完成內容、提供程式碼、提供測試結果、說明未解決問題、確認無重大邏輯漏洞。缺乏資料或 API 不得假裝完成回測；無法執行真實回測時必須標記為程式原型或待驗證模型。
