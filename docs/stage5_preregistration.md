# Stage 5 預先登記（Pre-registration）

> 登記時間：2026-11-27（以本文件的 git commit 時間為準）。
> **本文件 commit 時，沒有任何人看過 Validation 或 OOS 期間的績效**（Stage 4 報告只顯示 In-Sample）。
> 修改規則：本文件不得修改。若需變更，新增 `stage5_preregistration_v2.md` 並說明原因與時間。

---

## 1. 研究問題

**Fibonacci MA + Bollinger Bandwidth Squeeze Breakout + Weekly Trend Filter 在扣除交易成本後，是否具有統計上顯著、非過度擬合造成的正期望值？**

## 2. 假設

| 編號 | 假設 | 檢驗方式 |
|---|---|---|
| H1（主要） | 完整策略 E 的每筆期望值（R）> 0，且優於配對的隨機進場基準 | 隨機進場分布的百分位（單尾 p < 0.05）；交易層級 bootstrap 信賴區間 |
| H2 | MA 濾網與 BB 壓縮突破存在正交互作用（結合後的效果大於兩者相加） | 2×2 因子設計，交互作用項 |
| H3 | 週線濾網降低最大回撤，但不一定提高報酬（Prompt 02 H3） | 2×2×2 設計；E vs D 配對 block bootstrap |
| H4 | 成交量確認提高突破品質（Prompt 02 H5） | Leave-One-Out（E 移除成交量）；D vs C |
| H5 | 優勢在壓力成本下仍存在（Prompt 02 H6） | 壓力情境滑價上限下的期望值 |

多重檢定：H1–H5 以 Holm 法控制家族錯誤率；參數搜尋另以 Deflated Sharpe 與 PBO 處理。

## 3. 主要評估版本

- 策略：E（成交量定義：含當日 20 日均量 `include_today`）
- 成本：基準情境（手續費 6 折）、滑價區間下限 5 bps
- 移動停損：還原價追蹤（使用者決定 C1）
- 參數：`backend/config/strategy_default.toml` 的使用者初始設定（Prompt 03 第二節），**不使用任何依資料挑選的參數**

次要評估：同一版本在壓力情境、滑價上限 30 bps 下的結果。

## 4. OOS 及格門檻（全部來自使用者的 Prompt）

| # | 門檻 | 來源 |
|---|---|---|
| T1 | OOS Sharpe Ratio > 1 | Prompt 02 第九部分、Prompt 03 第十三節、Prompt 04 |
| T2 | OOS Profit Factor > 1.3 | 同上 |
| T3 | OOS CAGR > 0 | 同上 |
| T4 | 扣除成本後 OOS 每筆期望值 > 0 | 同上 |
| T5 | OOS 最大回撤 ≤ 25% | Prompt 03 未指定上限；採 Stage 1 研究預設值 25%（**待使用者確認**） |
| T6 | OOS 交易數 ≥ 100（不足則結論為「樣本不足」，不判定通過或失敗） | Stage 1 §9.3 |
| T7 | 壓力情境（滑價 30 bps）OOS 期望值 > 0 | Prompt 02 H6、Stage 1 §9.3 |
| T8 | Deflated Sharpe Ratio > 0.95 | Stage 1 §8.5 |
| T9 | PBO < 0.5 | Stage 1 §8.5 |
| T10 | 不依賴單一參數（參數高原）、不依賴單一年度（去掉最好的一年後期望值仍 > 0） | Prompt 03 第十三節 |

## 5. 最終判定（Prompt 03 第十七節）

| 判定 | 條件 |
|---|---|
| E. 缺乏統計優勢，建議放棄 | T4 不通過，或 H1 不顯著 |
| A. 值得繼續研究 | T4 通過但 T1、T2、T7 任一不通過，或 T6 樣本不足 |
| B. 回測有效，但尚未達到實盤標準 | T1–T5、T7 通過，但 T8–T10 任一不通過 |
| C. 具備模擬交易條件 | T1–T10 全部通過 |
| D. 具備小額實盤測試條件 | C 的條件 + 至少 3 個月模擬交易，且實際成交與回測滑價差異 < 50% |

## 6. 實驗與試驗次數上限

| 實驗 | 組合數 | 資料區間 |
|---|---|---|
| 2×2、2×2×2、A→E、Leave-One-Out | 固定組合（約 20） | In-Sample＋Validation |
| 隨機進場基準 | 200 次模擬 | In-Sample |
| 參數敏感度網格 | 事先固定，≤ 432 | In-Sample（挑選）、Validation（檢驗） |
| Walk-Forward | 同一網格 | In-Sample＋Validation，滾動 |

所有試驗（含失敗與不佳者）都寫入 `experiment_trials`，試驗總數用於 Deflated Sharpe。

## 7. 開發環境的限制

開發環境資料庫只有 72 檔股票（含刻意挑選的困難案例），**不是全市場**。在此樣本上的 Stage 5 結果只用來驗證方法與程式正確；**正式判定必須在使用者電腦的全市場資料庫上，依本文件重新執行一次。**
