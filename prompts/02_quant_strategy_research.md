# Prompt 02：Institutional Quant Research — Fibonacci MA + Bollinger Bandwidth Breakout + MTF

> **兩階段使用方式（不要一次要求全部）**
>
> - **Stage 1：策略研究與數學審核** — 提交本 Prompt，但只要求完成第 1–9 部分。
>   目標：檢查交易邏輯、確認公式、找出前視偏誤、確認進出場規則、設計消融實驗、確定回測架構。
>   → 已完成版本見 [`docs/stage1_strategy_research.md`](../docs/stage1_strategy_research.md)
> - **Stage 2：程式開發與實證回測** — 確認 Stage 1 後，再要求完成第 10–12 部分。
>   目標：建立 Python 專案、執行歷史回測、產出績效圖表、分析樣本外績效、壓力測試、評估實盤可行性。
>
> **整份研究最重要的問題**：獲利究竟來自 13/34/55 均線趨勢追蹤、布林帶寬壓縮突破，還是兩者結合後產生的額外 Alpha？
> 因此必須量化：單獨各模組績效、組合後增量績效、各模組邊際貢獻、訊號重複程度、是否改善樣本外績效。

---

## ROLE：Institutional Quant Researcher & Portfolio Manager

你是一位頂尖避險基金的資深量化研究員，具備：Quantitative Research、Systematic Trading、Technical Analysis、Time Series Analysis、Statistical Learning、Market Microstructure、Portfolio Construction、Python Algorithmic Trading、Backtesting & Strategy Optimization。

你的任務是將我提出的交易策略轉化為一套具有統計驗證能力、可重複執行、可實際回測及部署的量化交易系統。

你必須以研究者而非技術指標使用者的角度思考。不可以因為策略看起來合理，就預設它具有獲利能力。

你的首要任務是驗證策略是否真的具有正期望值（Positive Expectancy），以及這個優勢在扣除交易成本後是否仍然存在。

---

## 第一部分：我的核心交易策略

**策略名稱**：Fibonacci MA + Bollinger Bandwidth Breakout + Multi-Timeframe Confirmation

### A. Fibonacci Moving Average Trend Filter

三條費波那契週期均線：MA13、MA34、MA55。

- 基本多頭排列：`MA13 > MA34 > MA55`
- 基本空頭排列：`MA13 < MA34 < MA55`

研究：排列順序、均線斜率、均線間距、均線糾結程度、價格與均線相對位置、多頭排列持續時間。
必須研究均線排列是否能有效過濾盤整行情。不得只使用均線交叉作為進場訊號。

### B. Bollinger Bandwidth Compression

標準設定：Period = 20、Standard Deviation = 2。

`Bandwidth = (Upper Band - Lower Band) / Middle Band`

研究：Bandwidth 歷史百分位、是否處於低波動區間、是否由收縮轉為擴張、收縮持續時間、壓縮期間價格整理區間、突破後價格延續性。
優先研究以滾動百分位數辨識壓縮，而不是固定 Bandwidth 門檻。

### C. Breakout Confirmation

候選訊號：收盤價突破布林上軌；收盤價突破近期 N 日最高價；Bandwidth 由低百分位開始擴張；成交量高於歷史均量；突破時價格仍位於 MA13 及 MA34 上方。

不得預設所有條件都必須同時成立。必須透過消融實驗（Ablation Study）確認每個條件是否真正增加策略績效。

### D. Multi-Timeframe Filter

- 高週期：週線或日線，判斷主要趨勢。
- 交易週期：日線，確認突破。
- 低週期：60 分鐘線，研究更精確的進場時機。

高週期濾網候選：MA13 > MA34 > MA55；MA55 斜率為正；高週期收盤價位於 MA55 上方。

所有高週期資料只能使用當時已經完成的 K 線。禁止將尚未收盤的週線數據回填至過去日線。
如果使用低週期執行，必須另外驗證訊號時間、成交時間及成交價格。

---

## 第二部分：研究目標與參數

- 目標市場：[台股上市櫃／美股／其他]
- 股票範圍：[填入]
- 主要交易週期：[日線／60 分鐘／其他]
- 資金規模：[填入]
- 交易方向：[只做多／多空雙向]
- 交易方式：[現股／融資／期貨／其他]
- 程式語言：Python
- 主要套件：pandas、numpy、scipy、matplotlib、vectorbt 或 backtrader、yfinance 或其他可靠市場資料來源

資金風險限制：單筆最大風險 [預設 1%]、最大投組回撤 [待設定]、單一標的最大權重 [待設定]、融資融券 [待設定]。

若資料未提供，先採用明確標示的研究預設值，不得將預設值視為使用者已確認的設定。

## 第三部分：策略經濟直覺與 Alpha 假設

請先回答：

1. 這套策略試圖捕捉什麼市場行為？
2. 為什麼均線多頭排列可能具有預測能力？
3. 為什麼低波動壓縮後可能出現趨勢性突破？
4. 為什麼跨週期確認可能改善訊號品質？
5. 最可能在哪些市場環境獲利？
6. 最可能在哪些市場環境虧損？

提出至少三個可以被數據否證的研究假設，例如：

- H1：13/34/55 多頭排列下的布林壓縮突破，其未來 5、10、20 日報酬顯著優於沒有均線排列的突破。
- H2：低 Bandwidth 百分位後發生的向上突破，具有較高的趨勢延續機率。
- H3：加入高週期趨勢濾網後，能降低最大回撤，但不一定提高總報酬。

必須將假設轉化為可執行的統計檢定。

## 第四部分：數學公式與訊號定義

精確定義：MA13/34/55、均線斜率、均線間距、Bollinger Upper/Middle/Lower、Bandwidth、Bandwidth 滾動百分位、成交量確認、突破訊號、多週期趨勢濾網。

分別設計：Entry Long、Exit Long、Entry Short（若允許）、Exit Short、Regime Filter、Liquidity Filter。提供完整布林邏輯及數學公式。

不要直接把所有技術指標條件全部相乘或串接。先建立基礎策略，再逐項加入濾網，觀察每項濾網的邊際貢獻。

## 第五部分：策略模組化與消融實驗

- Strategy A：只有 MA13/34/55 趨勢排列。
- Strategy B：MA 排列 + Bollinger Bandwidth 壓縮。
- Strategy C：MA 排列 + Bandwidth 壓縮 + 價格突破。
- Strategy D：MA 排列 + Bandwidth 壓縮 + 價格突破 + 成交量確認。
- Strategy E：完整策略 + Multi-Timeframe Filter。

比較：CAGR、Sharpe、Sortino、MDD、Win Rate、Profit Factor、平均持有天數、交易次數、年化換手率、單筆期望報酬。

確認完整策略是否真的比簡單策略更有優勢。如果某個濾網降低交易次數，卻沒有改善風險調整後報酬，應考慮刪除。

## 第六部分：進出場與資金管理

進場方法：A. 突破當日收盤確認，下一交易日開盤進場；B. 突破後等待回測布林上軌或 MA13 再進場；C. 突破後使用 ATR 設定追蹤進場條件。比較勝率、平均獲利、平均虧損、MDD、交易頻率、風險報酬比。

出場方式：MA13 跌破、MA34 跌破、布林中軌跌破、ATR Trailing Stop、固定風險報酬比、Time Stop。

資金管理：`Position Size = (Account Equity × Risk Percentage) / (Entry Price - Stop Loss Price)`，再加入 ATR 波動率調整、單一標的權重上限、產業集中度限制、最大總曝險、回撤減倉機制。

倉位必須以可成交價格計算，並處理跳空穿越停損價的情況。

## 第七部分：市場微結構與真實交易成本

台股必須納入：買賣手續費、券商折扣、證交稅及適用稅率、雙邊滑價、最小交易單位、漲跌停限制、流動性限制、跳空缺口。

交易成本不得使用固定且不合理的極低數值。分別建立樂觀／一般／壓力成本情境。標示費率資料日期。

撮合規則：t 日收盤確認訊號，最早於 t+1 日開盤執行。禁止使用 t 日收盤後才知道的資訊卻假設以 t 日收盤價成交。漲停買不到、跌停賣不出必須模擬。

## 第八部分：回測與統計驗證

處理：Survivorship Bias、Look-ahead Bias、Data Snooping、Overfitting、Corporate Actions、Missing Data、Delisted Stocks。

資料切分：In-Sample 50%、Validation 25%、Out-of-Sample 25%（初始切分，依資料量調整）。

採用：Walk-Forward、Rolling Window、Expanding Window、Bootstrap、Monte Carlo、Parameter Sensitivity。

參數高原測試起點：MA 13/34/55 周邊；Bollinger Period 15–30；Std 1.5–2.5；Bandwidth Percentile 5%–30%；突破觀察期間 10–30 日。避免無限制搜尋。

不得只回報最佳參數；必須回報最佳、次佳、參數高原、不同市場環境穩健性、樣本外績效、多重假設檢定風險。

## 第九部分：績效評估與策略及格門檻

- 報酬：CAGR、Total Return、Annual Return。
- 風險：Annualized Volatility、Sharpe、Sortino、Calmar、MDD、MDD Duration。
- 交易品質：Win Rate、Payoff Ratio、Profit Factor、Average Trade Return、Expectancy、Average Holding Period、Turnover。

基準：Buy & Hold、MA13/34/55 單純趨勢策略、大盤指數、其他適合基準。

研究參考門檻：樣本外 Sharpe > 1；Profit Factor > 1.3；樣本外 CAGR 為正；MDD 符合限制；扣除成本後仍具正期望值；不同期間具一定穩健性。這些是研究參考標準，不是保證獲利的標準。

## 第十部分：完整 Python 量化回測系統（Stage 2）

模組：`data_loader.py`、`indicators.py`、`signal_generator.py`、`position_sizing.py`、`execution_engine.py`、`backtester.py`、`performance.py`、`visualization.py`、`main.py`。

必須包含：歷史資料讀取、缺失值處理、均線、布林通道、Bandwidth 百分位、多週期對齊、進出場訊號、倉位計算、停損及移動停利、交易成本、交易紀錄、淨值曲線、回撤曲線、績效報表。

程式紀律：所有訊號標示可用時間；使用 shift(1) 或等效時序處理；多週期只用已完成 K 線；嚴禁未來函數；成交價不得為尚未能確定的價格；每筆交易可追溯進出場原因；程式必須可實際執行，不可使用虛構 API。

完成後提供：安裝指令、資料格式、執行方式、預期輸出、常見錯誤處理。

## 第十一部分：策略壓力測試（Stage 2）

模擬：長期多頭、長期空頭、高波動震盪、低波動盤整、快速 V 型反轉、突破後立即假突破、極端跳空、流動性急速下降。

檢驗：均線延遲、布林壓縮假訊號、跨週期濾網是否過晚、移動停利是否過早、停損是否造成連續虧損。

提出至少兩種低相關性互補策略，但不得為了提高回測績效而任意加入。

## 第十二部分：最終量化研究報告（Stage 2）

輸出：核心邏輯、最終公式、最佳參數與穩健區間、消融結果、樣本內績效、樣本外績效、Walk-Forward、成本敏感度、市場環境績效、MDD 與風險、失效條件、是否具備實盤部署資格。

最終判斷：
- A. 值得進一步研究。
- B. 回測有效，但尚未達到實盤標準。
- C. 具有初步實盤部署條件。
- D. 策略缺乏統計優勢，建議放棄。

不得只因為回測報酬高就判斷策略有效。必須以樣本外績效、交易成本、統計穩健性及實際執行可行性作為最終依據。
