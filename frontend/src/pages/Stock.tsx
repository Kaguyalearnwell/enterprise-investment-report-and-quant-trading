import { useMemo, useState } from "react";
import { useApi, useDark } from "../hooks";
import { fmtNum, fmtPct, type Row } from "../api";
import { Chart } from "../Chart";
import { palette } from "../theme";
import { Loading, Table, VERSIONS, VERSION_LABEL } from "./common";

const ENTRY_BITS: Record<string, number> = { A: 1, B: 2, C: 4, D: 8, E: 16, S0: 32 };
const RANGES: [string, number][] = [["6 個月", 126], ["1 年", 252], ["3 年", 756], ["全部", 0]];

export function Stock({ id, date }: { id: string; date: string | null }) {
  const [query, setQuery] = useState("");
  const [version, setVersion] = useState("E");
  const [range, setRange] = useState(252);
  const [picked, setPicked] = useState<string | null>(date);
  const { data, error } = useApi<Row>(`/stocks/${id}`);
  const search = useApi<Row[]>(query.length >= 1 ? "/stocks/search" : null, { q: query });
  const ex = useApi<Row>(picked ? `/stocks/${id}/explain` : null, { date: picked ?? "" });
  const fund = useApi<Row>(`/stocks/${id}/fundamentals`);
  const p = palette[useDark() ? "dark" : "light"];

  const option = useMemo(() => {
    if (!data) return null;
    let bars: Row[] = data.bars.filter((b: Row) => b.has_price && b.close != null);
    if (picked && range) {
      const i = bars.findIndex((b) => b.date >= picked);
      if (i >= 0 && i < bars.length - range) bars = bars.slice(Math.max(0, i - range / 2), i + range / 2);
      else bars = bars.slice(-range);
    } else if (range) bars = bars.slice(-range);
    const dates = bars.map((b) => b.date);
    // Signals are computed on back-adjusted prices, so the candles are drawn on the same basis.
    const k = bars.map((b) => (b.adj_close && b.close ? b.adj_close / b.close : 1));
    const candles = bars.map((b, i) => [b.open * k[i], b.adj_close ?? b.close, b.low * k[i], b.high * k[i]]);
    const bit = ENTRY_BITS[version];
    const marks = bars.flatMap((b, i) => (b.entries != null && (b.entries & bit) ? [[b.date, b.low * k[i]]] : []));
    const line = (name: string, key: string, color: string, dash = false) => ({
      name, type: "line", data: bars.map((b) => b[key]), showSymbol: false, xAxisIndex: 0, yAxisIndex: 0,
      lineStyle: { width: dash ? 1 : 2, color, type: dash ? "dashed" : "solid" }, itemStyle: { color }, z: 3 });
    const ax = { axisLabel: { color: p.text2 }, axisLine: { lineStyle: { color: p.grid } }, splitLine: { lineStyle: { color: p.grid } } };
    return {
      backgroundColor: "transparent", animation: false, textStyle: { color: p.text },
      legend: { top: 0, textStyle: { color: p.text },
        data: ["K 線（還原）", "MA13", "MA34", "MA55", "布林上軌", "布林下軌", `${version} 訊號`, "成交量", "BBW 百分位"] },
      tooltip: { trigger: "axis", axisPointer: { type: "cross" }, valueFormatter: (v: any) => (typeof v === "number" ? fmtNum(v) : v) },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [{ left: 60, right: 20, top: 40, height: "52%" }, { left: 60, right: 20, top: "68%", height: "12%" },
        { left: 60, right: 20, top: "84%", height: "10%" }],
      xAxis: [0, 1, 2].map((g) => ({ type: "category", data: dates, gridIndex: g, ...ax, axisLabel: { show: g === 2, color: p.text2 } })),
      yAxis: [{ scale: true, gridIndex: 0, ...ax }, { gridIndex: 1, ...ax, axisLabel: { show: false } },
        { gridIndex: 2, min: 0, max: 100, interval: 50, ...ax }],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1, 2] }],
      series: [
        { name: "K 線（還原）", type: "candlestick", data: candles,
          itemStyle: { color: p.up, color0: p.down, borderColor: p.up, borderColor0: p.down } },
        line("MA13", "ma13", p.series[0]), line("MA34", "ma34", p.series[1]), line("MA55", "ma55", p.series[6]),
        line("布林上軌", "bb_upper", p.neutral, true), line("布林下軌", "bb_lower", p.neutral, true),
        { name: `${version} 訊號`, type: "scatter", data: marks, symbol: "triangle", symbolSize: 11, symbolOffset: [0, 12],
          itemStyle: { color: p.series[3], borderColor: p.surface, borderWidth: 2 }, z: 5 },
        { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: bars.map((b) => b.volume),
          itemStyle: { color: p.neutral } },
        { name: "BBW 百分位", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: bars.map((b) => b.bbw_pct), showSymbol: false,
          lineStyle: { width: 2, color: p.series[2] }, itemStyle: { color: p.series[2] },
          markLine: { silent: true, symbol: "none", data: [{ yAxis: 20 }], lineStyle: { color: p.text2, type: "dashed" },
            label: { formatter: "壓縮 20%", color: p.text2 } } },
      ],
    };
  }, [data, version, range, picked, p]);

  const f = fund.data?.metrics;
  return (
    <>
      <div className="controls">
        <input placeholder="搜尋代號或名稱" value={query} onChange={(e) => setQuery(e.target.value)} />
        {search.data && query && search.data.slice(0, 8).map((s) => (
          <a key={s.stock_id} href={`#/stock/${s.stock_id}`} onClick={() => { setQuery(""); setPicked(null); }}>
            {s.stock_id} {s.stock_name}</a>))}
      </div>
      {!data || !option ? <Loading error={error} /> : (
        <>
          <div className="card">
            <h2>{data.master.stock_id} {data.master.stock_name}
              <span className="muted"> ・{data.master.exchange}・{data.master.industry_category}
                {data.master.delisting_date ? `・已下市 ${data.master.delisting_date}` : ""}</span>
              {data.verdict && <> <span className={`badge ${data.verdict.verdict}`}>{data.verdict.verdict}</span></>}</h2>
            <div className="controls">
              <label>訊號版本 <select value={version} onChange={(e) => setVersion(e.target.value)}>
                {VERSIONS.map((v) => <option key={v} value={v}>{VERSION_LABEL[v]}</option>)}</select></label>
              {RANGES.map(([l, n]) => <button key={l} onClick={() => setRange(n)} style={range === n ? { fontWeight: 700 } : undefined}>{l}</button>)}
              <span className="muted">點 K 線查看當日條件</span>
            </div>
            <Chart option={option as any} height={620} onClick={(x: any) => x.name && setPicked(x.name)} />
            <p className="muted">K 線以還原價繪製（與訊號計算相同基準）；模擬成交使用原始價。紅漲綠跌。
              {data.verdict?.reasons && <> 資料品質：{data.verdict.reasons}</>}</p>
          </div>
          <div className="two">
            <div className="card">
              <h2>{picked ? `${picked} 訊號解釋` : "訊號解釋"}</h2>
              {!picked ? <div className="muted">點選圖上任一日。</div> : !ex.data ? <Loading error={ex.error} /> : !ex.data.available ?
                <div className="muted">{ex.data.reason ?? "該日沒有訊號資料"}</div> : (
                <>
                  <div>進場版本：{Object.entries(ex.data.entries).map(([v, ok]) =>
                    <span key={v} className={`badge ${ok ? "pass" : ""}`} style={{ marginRight: 4 }}>{v}{ok ? " ✓" : " ✗"}</span>)}</div>
                  <Table rows={ex.data.conditions} cols={[
                    ["passed", "", (v) => <span className={`badge ${v ? "pass" : "fail"}`}>{v ? "通過" : "未通過"}</span>],
                    ["rule", "條件"],
                    ["inputs", "數值", (v) => <span style={{ whiteSpace: "normal" }}>{Object.entries(v ?? {}).map(([k2, x]) => `${k2}=${typeof x === "number" ? fmtNum(x as number) : x}`).join("  ")}</span>],
                  ]} />
                  <p className="muted">{ex.data.note}</p>
                </>)}
            </div>
            <div className="card">
              <h2>基本面（{fund.data?.metrics?.as_of ?? ""} 時點可得）</h2>
              {!fund.data ? <Loading error={fund.error} /> : !f || f.latest_period == null ? <div className="muted">沒有基本面資料（開發資料庫只下載了少數股票）。</div> : (
                <>
                  <Table rows={[
                    ["最新財報期", f.latest_period], ["TTM 營收（億）", fmtNum(f.revenue_ttm / 1e8, 0)], ["營收年增", fmtPct(f.revenue_yoy)],
                    ["毛利率", fmtPct(f.gross_margin)], ["營業利益率", fmtPct(f.operating_margin)], ["淨利率", fmtPct(f.net_margin)],
                    ["ROE", fmtPct(f.roe)], ["ROA", fmtPct(f.roa)], ["營業現金流／淨利", fmtNum(f.cfo_to_net_income)],
                    ["自由現金流率", fmtPct(f.fcf_margin)], ["負債比", fmtPct(f.debt_ratio)], ["流動比", fmtNum(f.current_ratio)],
                    ["應收成長−營收成長", fmtPct(f.receivables_growth_minus_revenue_growth)],
                    ["存貨成長−營收成長", fmtPct(f.inventory_growth_minus_revenue_growth)], ["資料完整度", fmtPct(f.completeness, 0)],
                  ].map(([k2, v]) => ({ k: k2, v }))} cols={[["k", "指標"], ["v", "數值", undefined, true]]} />
                  <p className="muted">{fund.data.notice} 尚未評級（打分標準待你決定）。</p>
                </>)}
            </div>
          </div>
        </>
      )}
    </>
  );
}
