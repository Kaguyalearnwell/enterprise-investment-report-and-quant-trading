import { useMemo } from "react";
import { useApi, useDark } from "../hooks";
import { fmtNum, fmtPct, type Row } from "../api";
import { Chart } from "../Chart";
import { palette } from "../theme";
import { Loading, Table } from "./common";

const METRICS: [string, string, (v: number) => string][] = [
  ["total_return", "總報酬", fmtPct], ["cagr", "年化報酬", fmtPct], ["sharpe", "Sharpe", (v) => fmtNum(v)],
  ["sortino", "Sortino", (v) => fmtNum(v)], ["max_drawdown", "最大回撤", fmtPct], ["trades", "交易數", (v) => fmtNum(v, 0)],
  ["win_rate", "勝率", fmtPct], ["payoff_ratio", "賺賠比", (v) => fmtNum(v)], ["profit_factor", "獲利因子", (v) => fmtNum(v)],
  ["expectancy_r", "期望值（R）", (v) => fmtNum(v, 3)], ["exposure_mean", "平均曝險", fmtPct],
  ["total_costs", "總成本", (v) => fmtNum(v, 0)], ["average_holding_days", "平均持有日", (v) => fmtNum(v, 1)],
];

export function Backtests({ id, unseal }: { id: string | null; unseal: boolean }) {
  const list = useApi<Row[]>("/backtests");
  if (!id) return !list.data ? <Loading error={list.error} /> : (
    <div className="card"><h2>回測紀錄（只能新增、不能修改）</h2>
      <Table rows={list.data} cols={[
        ["run_id", "Run", (v) => <a href={`#/backtests/${v}`}>{v}</a>], ["created_at", "建立"], ["mode", "模式"],
        ["entry_version", "版本"], ["cost_scenario", "成本"], ["slippage_bps", "滑價 bps", undefined, true],
        ["trailing_basis", "移動停損基準"], ["universe_size", "股票數", undefined, true], ["status", "狀態"], ["code_version", "程式版本"]]} />
    </div>);
  return <Detail id={id} unseal={unseal} />;
}

function Detail({ id, unseal }: { id: string; unseal: boolean }) {
  const { data, error } = useApi<Row>(`/backtests/${id}`, { unseal: unseal || undefined });
  const p = palette[useDark() ? "dark" : "light"];
  const opts = useMemo(() => {
    if (!data) return null;
    const dates = data.daily.map((d: Row) => d.date);
    let peak = -Infinity;
    const dd = data.daily.map((d: Row) => { peak = Math.max(peak, d.equity); return d.equity / peak - 1; });
    const seg = data.segments as Record<string, [string, string]>;
    const area = Object.entries(seg).filter(([k]) => unseal || k !== "OOS").map(([k, [a, b]]) => [
      { name: k === "IS" ? "In-Sample" : k === "VALIDATION" ? "Validation" : "OOS", xAxis: a,
        itemStyle: { color: k === "VALIDATION" ? p.band : "transparent" }, label: { color: p.text2 } }, { xAxis: b }]);
    const ax = { axisLabel: { color: p.text2 }, axisLine: { lineStyle: { color: p.grid } }, splitLine: { lineStyle: { color: p.grid } } };
    const base = { backgroundColor: "transparent", animation: false, textStyle: { color: p.text },
      grid: { left: 70, right: 20, top: 30, bottom: 30 }, tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: dates, ...ax }, dataZoom: [{ type: "inside" }] };
    return {
      equity: { ...base, yAxis: { scale: true, ...ax },
        series: [{ name: "權益", type: "line", data: data.daily.map((d: Row) => d.equity), showSymbol: false,
          lineStyle: { width: 2, color: p.series[0] }, itemStyle: { color: p.series[0] },
          markArea: { silent: true, data: area } }],
        tooltip: { trigger: "axis", valueFormatter: (v: any) => fmtNum(v, 0) } },
      dd: { ...base, yAxis: { ...ax, axisLabel: { color: p.text2, formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
        series: [{ name: "回撤", type: "line", data: dd, showSymbol: false, areaStyle: { color: p.band },
          lineStyle: { width: 2, color: p.series[7] }, itemStyle: { color: p.series[7] } }],
        tooltip: { trigger: "axis", valueFormatter: (v: any) => fmtPct(v) } },
    };
  }, [data, unseal, p]);
  if (!data || !opts) return <Loading error={error} />;
  const segs = Object.keys(data.metrics).filter((k) => !k.startsWith("YEAR:"));
  const years = Object.keys(data.metrics).filter((k) => k.startsWith("YEAR:"));
  const r = data.run ?? {};
  return (
    <>
      {data.sealed ? (
        <div className="banner">OOS 封存中：圖表、交易與指標只顯示到 Validation 結束（{data.visible_until}）。
          OOS（{data.segments.OOS?.join(" ~ ")}）只能在研究預設值確認後看一次。</div>
      ) : <div className="banner">⚠️ 已解封 OOS。OOS 結果不得用來調整參數或挑選版本。<a href={`#/backtests/${data.run.run_id}`}>回到封存檢視</a></div>}
      <div className="card"><h2>{r.run_id}・{r.entry_version}・{r.mode}・成本 {r.cost_scenario}（滑價 {r.slippage_bps} bps）・{r.universe_size} 檔</h2>
        <div className="muted">移動停損基準 {r.trailing_basis}・{r.code_version}・{r.status}・建立 {r.created_at}</div>
        <Chart option={opts.equity as any} height={320} />
        <Chart option={opts.dd as any} height={180} />
        <p className="muted">淺色區塊 = Validation；左側為 In-Sample；兩者之間是 40 日禁區（embargo）。</p>
      </div>
      <div className="card"><h2>績效指標</h2>
        <Table rows={METRICS.map(([k, l, f]) => ({ l, ...Object.fromEntries(segs.map((s) => [s, data.metrics[s][k] == null ? "—" : f(data.metrics[s][k])])) }))}
          cols={[["l", "指標"], ...segs.map((s) => [s, s, undefined, true] as [string, string, undefined, boolean])]} />
        {years.length > 0 && <>
          <h2 style={{ marginTop: 14 }}>年度報酬</h2>
          <Table rows={years.map((y) => ({ y: y.slice(5), ret: fmtPct(data.metrics[y].return), wr: fmtPct(data.metrics[y].win_rate),
            n: fmtNum(data.metrics[y].trades, 0) }))} cols={[["y", "年"], ["ret", "報酬", undefined, true], ["wr", "勝率", undefined, true], ["n", "交易", undefined, true]]} />
          <p className="muted">封存時只顯示整年都在封存點以前的年度。</p></>}
      </div>
      <div className="card"><h2>模擬交易（{data.trades.length} 筆）</h2>
        <Table rows={data.trades} max={420} cols={[
          ["stock_id", "代號", (v, t) => <a href={`#/stock/${v}?date=${t.signal_date}`}>{v}</a>], ["stock_name", "名稱"],
          ["signal_date", "訊號日"], ["entry_date", "進場"], ["entry_price", "進場價", (v) => fmtNum(v), true],
          ["shares", "股數", (v) => fmtNum(v, 0), true], ["exit_date", "出場"], ["exit_price", "出場價", (v) => fmtNum(v), true],
          ["exit_reason", "原因", (v, t) => (t.censored ? <span className="muted">封存（出場在封存點之後）</span> : v)], ["r_multiple", "R", (v) => fmtNum(v), true], ["return_pct", "報酬", (v) => fmtPct(v), true],
          ["costs", "成本", (v) => fmtNum(v, 0), true]]} />
      </div>
    </>
  );
}
