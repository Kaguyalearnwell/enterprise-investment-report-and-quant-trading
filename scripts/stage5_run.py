"""Stage 5: run every pre-registered experiment on In-Sample + Validation and write the report.

    TQS_DATA_DIR=... python3 scripts/stage5_run.py [--quick]

--quick uses 20 random draws and a 9-combination grid (for testing the pipeline only).
All results are written to experiment_registry / experiment_trials and reports/stage5_results.md.
OOS is never simulated (ExperimentContext stops at the last Validation day).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config.settings import get_settings  # noqa: E402
from backend.config.strategy_config import load_config  # noqa: E402
from backend.core.logger import setup_logging  # noqa: E402
from backend.data.collectors.downloader import default_as_of  # noqa: E402
from backend.database.db_manager import DatabaseManager  # noqa: E402
from backend.experiments import designs as dz  # noqa: E402
from backend.experiments import robustness as rb  # noqa: E402
from backend.experiments import stats  # noqa: E402
from backend.experiments.context import ExperimentContext  # noqa: E402
from backend.experiments.registry import Registry, clean  # noqa: E402
from backend.strategies.fib_bb_weekly import VERSIONS  # noqa: E402
from backend.strategies.runner import SignalRunner  # noqa: E402

log = logging.getLogger("stage5")


def f(x, d=3):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    if isinstance(x, (int, np.integer)):
        return f"{x:,}"
    return f"{x:.{d}f}"


def pct(x):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.1f}%"


def metric_table(rows: dict[str, dict], seg: str) -> list[str]:
    cols = [("trades", "交易數", f), ("win_rate", "勝率", pct), ("profit_factor", "PF", f), ("expectancy_r", "期望值R", f),
            ("cagr", "CAGR", pct), ("max_drawdown", "MDD", pct), ("sharpe", "Sharpe", f), ("average_holding_days", "持有天數", f)]
    L = ["| 組合 | " + " | ".join(c[1] for c in cols) + " | 勝率95%區間 |", "|" + "---|" * (len(cols) + 2)]
    for label, m in rows.items():
        mm = m.get(seg, {})
        ci = f"{pct(mm.get('win_rate_ci_low'))}–{pct(mm.get('win_rate_ci_high'))}" if mm.get("trades") else "—"
        L.append(f"| {label} | " + " | ".join(fn(mm.get(k)) for k, _, fn in cols) + f" | {ci} |")
    return L


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    setup_logging()
    t0 = time.time()
    s = get_settings()
    db = DatabaseManager(s.db_path)
    db.initialize()
    cfg = load_config()
    as_of = default_as_of()
    vid = SignalRunner(db, cfg, as_of).register_version("include_today")
    ctx = ExperimentContext(db, cfg, vid)
    reg = Registry(db)
    n_random = 20 if args.quick else 200
    seg_is, seg_val = ctx.segments["IS"], ctx.segments["VALIDATION"]
    log.info("universe %d stocks, IS %s, VALIDATION %s, OOS %s (sealed)", len(ctx.ids), seg_is, seg_val,
             ctx.segments["OOS"])
    R: dict = {"segments": ctx.segments, "universe": len(ctx.ids), "excluded": ctx.excluded}

    # ---- 1. factor designs ---------------------------------------------------------------------------
    e1 = reg.start("factorial_2x2x2", "factorial", {"cells": {k: list(v) for k, v in dz.CELLS_2X2X2.items()}},
                   "IS+VALIDATION", "H2: MA x SQBO interaction > 0; H3: weekly filter lowers MDD", vid)
    cells = dz.run_cells(ctx, reg, e1, dz.CELLS_2X2X2)
    none_runs = dz.random_cell(ctx, ctx.signals_for(dz.MA), 20 if args.quick else 50, seed=11)
    none_tr = pd.concat([x["trades"] for x in none_runs], ignore_index=True)
    none_metrics = {seg: {k: float(np.nanmean([x["metrics"][seg].get(k, np.nan) for x in none_runs]))
                          for k in dz.KEY} for seg in ("IS", "VALIDATION")}
    for i, x in enumerate(none_runs):
        reg.trial(e1, f"none(random)#{i + 1}", {"template": "MA", "seed": 11}, "IS+VALIDATION",
                  {seg: {k: x["metrics"][seg].get(k) for k in dz.KEY} for seg in x["metrics"]})
    eff = {seg: dz.effects(ctx, cells, none_tr, seg) for seg in ("IS", "VALIDATION")}
    # weekly effect: mean over the 3 with/without-W pairs (expectancy), and E vs D MDD comparison later
    w_pairs = [("MA+SQBO+W", "MA+SQBO"), ("MA+W", "MA"), ("SQBO+W", "SQBO")]
    weekly = {seg: {f"{a} vs {b}": {"delta_expectancy_r": (cells[a]["metrics"][seg].get("expectancy_r") or np.nan)
                                    - (cells[b]["metrics"][seg].get("expectancy_r") or np.nan),
                                    "delta_mdd": (cells[a]["metrics"][seg].get("max_drawdown") or np.nan)
                                    - (cells[b]["metrics"][seg].get("max_drawdown") or np.nan)}
                    for a, b in w_pairs} for seg in ("IS", "VALIDATION")}
    reg.finish(e1, {"effects": eff, "weekly": weekly})
    R["factorial"] = {"cells": {k: v["metrics"] for k, v in cells.items()}, "none": none_metrics, "effects": eff,
                      "weekly": weekly}
    log.info("factorial done (%.0fs)", time.time() - t0)

    # ---- 2. A-E and leave-one-out --------------------------------------------------------------------
    e2 = reg.start("ablation_A_E_LOO", "ablation", {"A_E": {k: list(v) for k, v in VERSIONS.items()},
                                                    "LOO": {k: list(v) for k, v in dz.LOO.items()}},
                   "IS+VALIDATION", "H4: volume confirmation improves breakout quality", vid)
    abl = dz.run_cells(ctx, reg, e2, {**VERSIONS, **dz.LOO})
    reg.finish(e2, {k: v["metrics"] for k, v in abl.items()})
    R["ablation"] = {k: v["metrics"] for k, v in abl.items()}
    log.info("ablation done (%.0fs)", time.time() - t0)

    # ---- 3. matched random-entry benchmark ---------------------------------------------------------------
    e3 = reg.start("random_benchmark_E", "random_benchmark", {"template": "E", "n": n_random, "seed": 7},
                   "IS+VALIDATION", "H1: E beats matched random entry", vid)
    rnd = dz.random_cell(ctx, ctx.signals_for(VERSIONS["E"]), n_random, seed=7)
    for i, x in enumerate(rnd):
        reg.trial(e3, f"random#{i + 1}", {"seed": 7, "draw": i + 1}, "IS+VALIDATION",
                  {seg: {k: x["metrics"][seg].get(k) for k in dz.KEY} for seg in x["metrics"]})
    bench = {seg: dz.random_benchmark(ctx, abl["E"], rnd, seg) for seg in ("IS", "VALIDATION")}
    reg.finish(e3, bench)
    R["random_benchmark"] = bench
    log.info("random benchmark done (%.0fs)", time.time() - t0)

    # ---- 4. redundancy -----------------------------------------------------------------------------------
    red = dz.redundancy(ctx, seg_val[1])
    inc = dz.incremental_filtering(ctx, seg_val[1])
    R["redundancy"] = red.to_dict("records")
    R["incremental"] = inc.to_dict("records")

    # ---- 5. bootstrap, Monte Carlo, cost and basis variants -------------------------------------------------
    e_daily = abl["E"]["daily"]
    rets = {k: rb.daily_returns(abl[k]["daily"]) for k in ("E", "D", "A")}
    boot = {}
    for seg, (a, b) in (("IS", seg_is), ("VALIDATION", seg_val)):
        rE = rets["E"][(rets["E"].index >= a) & (rets["E"].index <= b)]
        sh = stats.bootstrap([rE.to_numpy()], stats.sharpe, n_boot=500 if args.quick else 2000, seed=1)
        out = {"sharpe": stats.sharpe(rE.to_numpy()), "sharpe_ci95": [float(np.percentile(sh, 2.5)), float(np.percentile(sh, 97.5))]}
        for other in ("D", "A"):
            ro = rets[other].reindex(rE.index).fillna(0.0)
            d = stats.bootstrap([rE.to_numpy(), ro.to_numpy()], lambda x, y: stats.sharpe(x) - stats.sharpe(y),
                                n_boot=500 if args.quick else 2000, seed=2)
            mdd = stats.bootstrap([rE.to_numpy(), ro.to_numpy()],
                                  lambda x, y: rb.mdd_from_returns(x) - rb.mdd_from_returns(y),
                                  n_boot=300 if args.quick else 1000, seed=3)
            out[f"sharpe_E_minus_{other}"] = {"estimate": stats.sharpe(rE.to_numpy()) - stats.sharpe(ro.to_numpy()),
                                              "ci95": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))],
                                              "p_le_zero": float((d <= 0).mean())}
            out[f"mdd_E_minus_{other}"] = {"estimate": rb.mdd_from_returns(rE.to_numpy()) - rb.mdd_from_returns(ro.to_numpy()),
                                           "ci95": [float(np.percentile(mdd, 2.5)), float(np.percentile(mdd, 97.5))],
                                           "p_ge_zero": float((mdd >= 0).mean())}
        tE = dz._seg_trades(ctx, abl["E"]["trades"], seg)
        out["expectancy_r"] = dz.trade_mean_ci(tE["r_multiple"].astype(float).dropna().to_numpy())
        out["monte_carlo"] = stats.mc_reshuffle(tE["pnl"].astype(float).to_numpy(), float(cfg["account"]["initial_capital"]),
                                                n_sims=500 if args.quick else 5000) if len(tE) else {}
        boot[seg] = out
    variants = {}
    e5 = reg.start("cost_and_basis_E", "sensitivity", {"costs": ["optimistic", "base", "stress"], "ends": ["low", "high"],
                                                       "basis": ["adjusted", "raw"]}, "IS+VALIDATION",
                   "H5: positive expectancy under stress costs", vid)
    sigE = ctx.signals_for(VERSIONS["E"])
    for sc in ("optimistic", "base", "stress"):
        for end in ("low", "high"):
            tr, dl = ctx.simulate(sigE, cost_scenario=sc, slippage_end=end)
            m = ctx.metrics(tr, dl)
            variants[f"{sc}/{end}"] = m
            reg.trial(e5, f"{sc}/{end}", {"cost": sc, "slippage_end": end}, "IS+VALIDATION",
                      {seg: {k: m[seg].get(k) for k in dz.KEY} for seg in m})
            if (sc, end) == ("stress", "high"):
                stress_tr = tr
    tr, dl = ctx.simulate(sigE, basis="raw")
    variants["raw-basis trailing"] = ctx.metrics(tr, dl)
    reg.trial(e5, "raw-basis trailing", {"basis": "raw"}, "IS+VALIDATION",
              {seg: {k: variants["raw-basis trailing"][seg].get(k) for k in dz.KEY} for seg in ("IS", "VALIDATION")})
    stress_ci = {seg: dz.trade_mean_ci(dz._seg_trades(ctx, stress_tr, seg)["r_multiple"].astype(float).dropna().to_numpy())
                 for seg in ("IS", "VALIDATION")}
    reg.finish(e5, {"variants": variants, "stress_high_expectancy_ci": stress_ci})
    R["bootstrap"], R["variants"], R["stress_ci"] = boot, variants, stress_ci
    log.info("bootstrap/variants done (%.0fs)", time.time() - t0)

    # ---- 6. parameter grid, exit grid, plateau, walk-forward, PBO, DSR --------------------------------------
    e6 = reg.start("parameter_grid", "grid", {"signal_grid": {k: [list(x) if isinstance(x, tuple) else x for x in v]
                                                              for k, v in rb.GRID.items()},
                                              "exit_grid": rb.EXIT_GRID, "selection": "plateau centre on IS Sharpe"},
                   "IS+VALIDATION", "parameter robustness (T10), DSR (T8), PBO (T9)", vid)
    inputs = ctx.inputs()
    grid = rb.grid_signals(inputs, cfg, (ctx.start, ctx.end), workers=4, limit=9 if args.quick else None)
    log.info("grid signals computed: %d combinations (%.0fs)", len(grid), time.time() - t0)
    grid_rets, grid_is = {}, {}
    for k, (combo, sig) in grid.items():
        tr, dl = ctx.simulate(sig)
        m = ctx.metrics(tr, dl)
        r = rb.daily_returns(dl) if not dl.empty else pd.Series(dtype=float)
        grid_rets[k] = r
        grid_is[k] = m["IS"]
        reg.trial(e6, "grid:" + json.dumps(clean(combo)), {"combo": combo}, "IS+VALIDATION",
                  {seg: {kk: m[seg].get(kk) for kk in dz.KEY} for seg in m})
    exit_rets = {}
    for st_m in rb.EXIT_GRID["stop_atr_mult"]:
        for tr_m in rb.EXIT_GRID["trail_atr_mult"]:
            c2 = json.loads(json.dumps(cfg))
            c2["risk"]["stop_atr_mult"], c2["risk"]["trail_atr_mult"] = st_m, tr_m
            tr, dl = ctx.simulate(sigE, cfg=c2)
            m = ctx.metrics(tr, dl)
            exit_rets[(st_m, tr_m)] = (rb.daily_returns(dl), m)
            reg.trial(e6, f"exit:stop{st_m}/trail{tr_m}", {"stop_atr_mult": st_m, "trail_atr_mult": tr_m},
                      "IS+VALIDATION", {seg: {kk: m[seg].get(kk) for kk in dz.KEY} for seg in m})
    is_sharpe = {k: grid_is[k].get("sharpe", np.nan) for k in grid_is}
    plateau = rb.plateau_scores(is_sharpe)
    sel = rb.select_plateau(is_sharpe)
    best_raw = max((k for k in is_sharpe if not math.isnan(is_sharpe[k] or np.nan)), key=lambda k: is_sharpe[k])
    ranked = sorted(is_sharpe, key=lambda k: -(is_sharpe[k] if not math.isnan(is_sharpe[k]) else -9))
    # PBO on IS daily returns, DSR of the baseline with ALL trials' Sharpe ratios
    idx_is = [d for d in grid_rets[rb.key(rb.BASELINE)].index if seg_is[0] <= d <= seg_is[1]]
    mat = np.column_stack([grid_rets[k].reindex(idx_is).fillna(0.0).to_numpy() for k in grid_rets])
    pbo = stats.pbo_cscv(mat, 16) if mat.shape[1] >= 2 else {}
    all_trial_sr = [stats.sharpe(grid_rets[k].reindex(idx_is).fillna(0.0).to_numpy()) / math.sqrt(252) for k in grid_rets]
    all_trial_sr += [stats.sharpe(v[0][(v[0].index >= seg_is[0]) & (v[0].index <= seg_is[1])].to_numpy()) / math.sqrt(252)
                     for v in exit_rets.values()]
    base_r = grid_rets[rb.key(rb.BASELINE)].reindex(idx_is).fillna(0.0).to_numpy()
    dsr = stats.deflated_sharpe(base_r, all_trial_sr)
    wf = rb.walk_forward(grid_rets, ctx.start, ctx.end)
    wf_eff = (np.nanmean([w["test_sharpe"] for w in wf]) / np.nanmean([w["train_sharpe"] for w in wf])
              if wf and np.nanmean([w["train_sharpe"] for w in wf]) else math.nan)
    # T10 parts: plateau around the baseline, and "drop the best year"
    base_tr = dz._seg_trades(ctx, abl["E"]["trades"], "IS")
    by_year = base_tr.groupby(base_tr["exit_date"].str[:4])["r_multiple"].agg(["count", "mean", "sum"]) if len(base_tr) else pd.DataFrame()
    drop_best = (float((base_tr["r_multiple"].sum() - by_year["sum"].max()) / (len(base_tr) - by_year.loc[by_year["sum"].idxmax(), "count"]))
                 if len(by_year) > 1 else math.nan)
    R["grid"] = {"n_signal_combos": len(grid), "n_exit_combos": len(exit_rets), "selected_plateau": sel,
                 "selected_plateau_score": plateau.get(sel), "best_single": best_raw, "best_single_sharpe": is_sharpe[best_raw],
                 "baseline_is_sharpe": is_sharpe.get(rb.key(rb.BASELINE)), "baseline_plateau": plateau.get(rb.key(rb.BASELINE)),
                 "baseline_rank": ranked.index(rb.key(rb.BASELINE)) + 1 if rb.key(rb.BASELINE) in ranked else None,
                 "top5": [(k, is_sharpe[k], grid_is[k].get("trades"), grid_is[k].get("expectancy_r")) for k in ranked[:5]],
                 "share_positive_sharpe": float(np.mean([v > 0 for v in is_sharpe.values() if not math.isnan(v)])),
                 "exit_grid": {f"stop{k[0]}/trail{k[1]}": v[1]["IS"] for k, v in exit_rets.items()},
                 "selected_validation": {"sharpe": None}}
    sel_combo = grid[sel][0]
    trv, dlv = ctx.simulate(grid[sel][1])
    R["grid"]["selected_validation"] = ctx.metrics(trv, dlv)["VALIDATION"]
    R["grid"]["baseline_validation"] = abl["E"]["metrics"]["VALIDATION"]
    R["grid"]["selected_combo"] = sel_combo
    R["pbo"], R["dsr"], R["walk_forward"], R["wf_efficiency"] = pbo, dsr, wf, wf_eff
    R["drop_best_year"] = {"by_year": by_year.reset_index().to_dict("records") if len(by_year) else [],
                           "expectancy_without_best_year": drop_best}
    reg.finish(e6, {"pbo": pbo, "dsr": dsr, "walk_forward": wf, "wf_efficiency": wf_eff,
                    "selected": list(sel), "baseline_rank": R["grid"]["baseline_rank"]})
    log.info("grid done (%.0fs)", time.time() - t0)

    # ---- 7. market regimes -------------------------------------------------------------------------------
    idx = db.read_sql("SELECT date, value FROM benchmark_index WHERE index_id='TAIEX' ORDER BY date")
    reg_df = rb.regimes(pd.Series(idx["value"].to_numpy(), index=idx["date"]))
    t_all = abl["E"]["trades"]
    t_all = t_all[t_all["is_closed"] == 1].copy()
    t_all["trend"] = t_all["signal_date"].map(reg_df["trend"])
    t_all["volatility"] = t_all["signal_date"].map(reg_df["volatility"])
    regime_tab = {}
    for col in ("trend", "volatility"):
        g = t_all.groupby(col)
        regime_tab[col] = pd.DataFrame({"trades": g.size(), "win_rate": g["return_pct"].apply(lambda r: (r > 0.001).mean()),
                                        "expectancy_r": g["r_multiple"].mean(),
                                        "profit_factor": g["pnl"].apply(lambda p: p[p > 0].sum() / -p[p < 0].sum()
                                                                        if (p < 0).any() else np.nan)}).reset_index().to_dict("records")
    R["regimes"] = regime_tab
    R["regime_day_share"] = {c: reg_df.loc[(reg_df.index >= ctx.start) & (reg_df.index <= ctx.end), c].value_counts(normalize=True).to_dict()
                             for c in ("trend", "volatility")}

    # ---- 8. hypotheses with Holm ---------------------------------------------------------------------
    p = {"H1 E beats random (expectancy, IS)": bench["IS"].get("expectancy_r", {}).get("p_value_one_sided", 1.0),
         "H2 MA x SQBO interaction > 0 (IS)": eff["IS"].get("interaction", {}).get("p_le_zero", 1.0) if eff["IS"].get("available") else 1.0,
         "H3 weekly lowers MDD: E vs D (IS)": boot["IS"]["mdd_E_minus_D"]["p_ge_zero"],
         "H5 stress expectancy > 0 (IS)": stress_ci["IS"].get("p_le_zero", 1.0)}
    loo_vol = abl["E-volume_confirm"]["metrics"]["IS"].get("expectancy_r")
    R["H4_volume_LOO"] = {"E": abl["E"]["metrics"]["IS"].get("expectancy_r"), "E_without_volume": loo_vol}
    R["holm"] = stats.holm(p)
    R["seconds"] = time.time() - t0
    R["total_trials_in_registry"] = reg.total_trials()

    out_json = ROOT / "reports" / "stage5_results.json"
    out_json.write_text(json.dumps(clean(R), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    write_report(R, ROOT / "reports" / "stage5_results.md", args.quick)
    log.info("stage 5 done in %.0f s", time.time() - t0)
    return 0


def write_report(R: dict, path: Path, quick: bool) -> None:
    sg = R["segments"]
    L = ["# Stage 5 統計驗證結果（自動產生）", "",
         f"- 樣本：開發環境 {R['universe']} 檔（**非全市場**）；排除 {R['excluded']}",
         f"- In-Sample {sg['IS'][0]} ~ {sg['IS'][1]}；Validation {sg['VALIDATION'][0]} ~ {sg['VALIDATION'][1]}；"
         f"OOS {sg['OOS'][0]} ~ {sg['OOS'][1]} **封存，未模擬**",
         f"- 登記的試驗總數（實驗登記表）：{R['total_trials_in_registry']}；執行時間 {R['seconds'] / 60:.1f} 分鐘"
         + ("；**QUICK 模式（只用於測試流程）**" if quick else ""),
         "- 研究預設值（追價 1 ATR、投組風險計算、排序、零股、市場環境分類）**尚待使用者確認**。", ""]
    fac = R["factorial"]
    for seg, name in (("IS", "In-Sample"), ("VALIDATION", "Validation")):
        L += [f"## 1. 因子設計（{name}）", ""]
        rows = {**fac["cells"], "none（隨機，平均）": fac["none"]}
        L += metric_table(rows, seg)
        e = fac["effects"][seg]
        if e.get("available"):
            L += ["", "| 效果（期望值 R） | 估計 | 95% 區間 | P(≤0) |", "|---|---|---|---|"]
            for k, nm in (("ma_main", "MA 主效果"), ("sqbo_main", "壓縮突破主效果"), ("interaction", "交互作用")):
                L.append(f"| {nm} | {f(e[k]['estimate'])} | {f(e[k]['ci95'][0])} ~ {f(e[k]['ci95'][1])} | {f(e[k]['p_le_zero'])} |")
        L += ["", "| 加入週線 | Δ期望值R | ΔMDD |", "|---|---|---|"]
        L += [f"| {k} | {f(v['delta_expectancy_r'])} | {pct(v['delta_mdd'])} |" for k, v in fac["weekly"][seg].items()]
        L.append("")
    for seg, name in (("IS", "In-Sample"), ("VALIDATION", "Validation")):
        L += [f"## 2. A→E 與 Leave-One-Out（{name}）", ""] + metric_table(R["ablation"], seg) + [""]
    L += ["## 3. 隨機進場基準（E 的訊號日期與筆數，隨機選股）", ""]
    for seg in ("IS", "VALIDATION"):
        b = R["random_benchmark"][seg]
        L += [f"**{seg}**（{b['n_random']} 次）", "", "| 指標 | 策略 E | 隨機中位數 | 隨機 5%–95% | 策略百分位 | 單尾 p |", "|---|---|---|---|---|---|"]
        for k in ("expectancy_r", "profit_factor", "sharpe", "cagr", "win_rate", "max_drawdown"):
            if k in b:
                x = b[k]
                L.append(f"| {k} | {f(x['strategy'])} | {f(x['random_median'])} | {f(x['random_p05'])} ~ {f(x['random_p95'])} | "
                         f"{pct(x['percentile'])} | {f(x['p_value_one_sided'])} |")
        L.append("")
    L += ["## 4. 條件重複性（可交易股票日，IS＋Validation）", "", "| X | Y | P(X) | P(Y) | P(Y\\|X) | Phi | MI（bits） |",
          "|---|---|---|---|---|---|---|"]
    L += [f"| {r['x']} | {r['y']} | {pct(r['p_x'])} | {pct(r['p_y'])} | {pct(r['p_y_given_x'])} | {f(r['phi'])} | {f(r['mutual_information_bits'], 4)} |"
          for r in R["redundancy"]]
    L += ["", "| 條件 | 通過其他所有條件的股票日 | 再加此條件後 | 此條件額外排除比例 |", "|---|---|---|---|"]
    L += [f"| {r['condition']} | {r['days_passing_others']:,} | {r['days_passing_all']:,} | {pct(r['share_removed'])} |" for r in R["incremental"]]
    L += ["", "## 5. Bootstrap 與 Monte Carlo（E）", ""]
    for seg in ("IS", "VALIDATION"):
        b = R["bootstrap"][seg]
        er = b["expectancy_r"]
        L += [f"**{seg}**", "",
              f"- Sharpe {f(b['sharpe'])}（95% 區間 {f(b['sharpe_ci95'][0])} ~ {f(b['sharpe_ci95'][1])}）",
              f"- 每筆期望值 {f(er.get('mean'))}R（95% 區間 {f(er.get('ci95', [None, None])[0])} ~ {f(er.get('ci95', [None, None])[1])}，P(≤0) = {f(er.get('p_le_zero'))}，n = {er.get('n')}）"]
        for o in ("D", "A"):
            x, y = b[f"sharpe_E_minus_{o}"], b[f"mdd_E_minus_{o}"]
            L.append(f"- Sharpe E − {o}：{f(x['estimate'])}（{f(x['ci95'][0])} ~ {f(x['ci95'][1])}，P(≤0) = {f(x['p_le_zero'])}）；"
                     f"MDD E − {o}：{pct(y['estimate'])}（{pct(y['ci95'][0])} ~ {pct(y['ci95'][1])}，P(≥0) = {f(y['p_ge_zero'])}）")
        mc = b.get("monte_carlo") or {}
        if mc:
            L.append(f"- 交易順序 Monte Carlo：MDD 中位數 {pct(mc['mdd_p50'])}、95% {pct(mc['mdd_p95'])}、99% {pct(mc['mdd_p99'])}；"
                     f"最長連敗中位數 {f(mc['losing_streak_p50'], 0)}、95% {f(mc['losing_streak_p95'], 0)}")
        L.append("")
    L += ["## 6. 成本與停損基礎（E）", ""]
    for seg in ("IS", "VALIDATION"):
        L += [f"**{seg}**", ""] + metric_table(R["variants"], seg) + [""]
        s = R["stress_ci"][seg]
        L.append(f"壓力情境（滑價 30 bps）每筆期望值 {f(s.get('mean'))}R，95% 區間 {f(s.get('ci95', [None, None])[0])} ~ "
                 f"{f(s.get('ci95', [None, None])[1])}，P(≤0) = {f(s.get('p_le_zero'))}")
        L.append("")
    g = R["grid"]
    L += ["## 7. 參數網格、高原、Walk-Forward、PBO、Deflated Sharpe（IS 挑選、Validation 檢驗）", "",
          f"- 訊號參數 {g['n_signal_combos']} 組 + 出場參數 {g['n_exit_combos']} 組",
          f"- IS Sharpe > 0 的比例：{pct(g['share_positive_sharpe'])}",
          f"- 使用者原始參數（13/34/55、2σ、20%、6 日、1.5 倍）：IS Sharpe {f(g['baseline_is_sharpe'])}，排名 {g['baseline_rank']} / {g['n_signal_combos']}，"
          f"鄰域平均 {f(g['baseline_plateau'])}",
          f"- 高原中心（挑選規則）：{g['selected_plateau']}，鄰域平均 Sharpe {f(g['selected_plateau_score'])}",
          f"- 單點最佳：{g['best_single']}，IS Sharpe {f(g['best_single_sharpe'])}（**不採用**，可能是孤立尖峰）", "",
          "| 版本 | Validation 交易數 | 勝率 | PF | 期望值R | Sharpe | MDD |", "|---|---|---|---|---|---|---|"]
    for nm, m in (("原始參數", g["baseline_validation"]), ("高原中心", g["selected_validation"])):
        L.append(f"| {nm} | {f(m.get('trades'))} | {pct(m.get('win_rate'))} | {f(m.get('profit_factor'))} | {f(m.get('expectancy_r'))} | "
                 f"{f(m.get('sharpe'))} | {pct(m.get('max_drawdown'))} |")
    L += ["", "IS Sharpe 前 5 名：", "", "| 組合（MA, σ, 壓縮%, 回看, 量倍數） | IS Sharpe | 交易數 | 期望值R |", "|---|---|---|---|"]
    L += [f"| {k} | {f(s)} | {f(n)} | {f(e)} |" for k, s, n, e in g["top5"]]
    L += ["", "出場參數（IS）：", "", "| 停損/移動停利 | 交易數 | PF | 期望值R | Sharpe | MDD |", "|---|---|---|---|---|---|"]
    L += [f"| {k} | {f(m.get('trades'))} | {f(m.get('profit_factor'))} | {f(m.get('expectancy_r'))} | {f(m.get('sharpe'))} | {pct(m.get('max_drawdown'))} |"
          for k, m in g["exit_grid"].items()]
    L += ["", "Walk-Forward（訓練 3 年、測試 1 年，只在 IS＋Validation）：", "",
          "| 訓練 | 測試 | 挑選組合 | 訓練 Sharpe | 測試 Sharpe | 原始參數測試 Sharpe |", "|---|---|---|---|---|---|"]
    L += [f"| {w['train'][0][:4]}–{w['train'][1][:4]} | {w['test'][0][:4]} | {w['selected']} | {f(w['train_sharpe'])} | {f(w['test_sharpe'])} | {f(w['baseline_test_sharpe'])} |"
          for w in R["walk_forward"]]
    L += ["", f"- Walk-Forward 效率（測試 Sharpe ÷ 訓練 Sharpe）：{f(R['wf_efficiency'])}"]
    p, d = R["pbo"], R["dsr"]
    if p:
        L.append(f"- **PBO = {f(p['pbo'])}**（CSCV {p['n_blocks']} 區塊、{p['n_combinations']:,} 種切法、{p['n_trials']} 組）；"
                 f"IS 最佳組合在樣本外虧損的機率 {f(p['prob_oos_loss_of_best'])}；IS→OOS Sharpe 斜率 {f(p['is_vs_oos_slope'])}")
    L.append(f"- **Deflated Sharpe = {f(d['dsr'])}**（原始參數 IS 年化 Sharpe {f(d['sharpe_annual'])}、試驗數 {d['n_trials']}、"
             f"偏態 {f(d['skew'])}、峰態 {f(d['kurtosis'])}）；PSR(>0) = {f(d['psr_vs_zero'])}")
    db_ = R["drop_best_year"]
    L.append(f"- 去掉最好的一年後，IS 每筆期望值：{f(db_['expectancy_without_best_year'])}R")
    L += ["", "## 8. 市場環境（E，IS＋Validation，研究預設分類）", ""]
    for col, nm in (("trend", "趨勢"), ("volatility", "波動")):
        L += [f"**{nm}**（期間天數占比：{', '.join(f'{k} {pct(v)}' for k, v in R['regime_day_share'][col].items())}）", "",
              "| 環境 | 交易數 | 勝率 | 期望值R | PF |", "|---|---|---|---|---|"]
        L += [f"| {r[col]} | {r['trades']} | {pct(r['win_rate'])} | {f(r['expectancy_r'])} | {f(r['profit_factor'])} |" for r in R["regimes"][col]]
        L.append("")
    L += ["## 9. 預先登記假設（IS，Holm 校正，α = 0.05）", "", "| 假設 | p | Holm 門檻 | 拒絕虛無假設 |", "|---|---|---|---|"]
    L += [f"| {k} | {f(v['p'])} | {f(v['threshold'], 4)} | {'是' if v['reject_null'] else '否'} |" for k, v in R["holm"].items()]
    h4 = R["H4_volume_LOO"]
    L += ["", f"H4（成交量）：E 期望值 {f(h4['E'])}R；E 移除成交量條件 {f(h4['E_without_volume'])}R（描述性比較）", "",
          "## 10. 限制", "",
          "- 72 檔開發樣本、含刻意挑選的困難案例：**結果用於驗證方法，不能作為策略是否有效的結論**。",
          "- 正式結論須在全市場資料庫上依預先登記重新執行，並經使用者確認研究預設值後才解封 OOS。",
          "- 處置股、全額交割股未模擬；產業為目前分類；除權息以參考價做價值保留近似。"]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
