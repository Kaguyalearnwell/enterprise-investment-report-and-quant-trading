import { useApi, useDark } from "../hooks";
import { fmtNum, fmtPct, type Row } from "../api";
import { Chart } from "../Chart";
import { palette } from "../theme";
import { Loading, Tile, VERSIONS, VERSION_LABEL } from "./common";

export function Overview() {
  const { data, error } = useApi<Row>("/overview");
  const p = palette[useDark() ? "dark" : "light"];
  if (!data) return <Loading error={error} />;
  const m = data.market ?? {};
  const counts = VERSIONS.map((v) => data.signals_today?.[v] ?? 0);
  const dq = data.data_quality_verdicts ?? {};
  return (
    <>
      <div className="grid">
        <Tile label="有股價的股票" value={fmtNum(data.stocks_with_prices, 0)} sub={`主檔上市櫃普通股 ${fmtNum(data.common_listed, 0)} 檔`} />
        <Tile label="最新股價日" value={data.last_price_date ?? "—"} sub={`最新訊號日 ${data.last_signal_date ?? "—"}`} />
        <Tile label="當日可交易（流動性＋資料）" value={fmtNum(data.eligible_today, 0)} />
        <Tile label="市場（樣本等權指數）" value={m.above_ma200 == null ? "—" : m.above_ma200 ? "在 200 日均線上" : "在 200 日均線下"}
          sub={`20 日年化波動 ${fmtPct(m.vol20_annual)}`} />
        <Tile label="資料品質" value={<span>{dq.PASS ?? 0} <span className="badge PASS">PASS</span></span>}
          sub={`WARNING ${dq.WARNING ?? 0}・FAIL ${dq.FAIL ?? 0}・下載失敗 ${data.failed_downloads}`} />
      </div>
      {data.stocks_with_prices < data.common_listed && (
        <div className="banner">目前資料庫只有 {data.stocks_with_prices} 檔（開發樣本），不是全市場；任何統計結果都不能當成全市場結論。</div>
      )}
      <div className="card">
        <h2>{data.last_signal_date} 各版本訊號數</h2>
        <Chart height={260} option={{
          backgroundColor: "transparent", textStyle: { color: p.text },
          grid: { left: 150, right: 40, top: 10, bottom: 30 },
          tooltip: { trigger: "item", formatter: (x: any) => `${x.name}：${x.value} 檔` },
          xAxis: { type: "value", minInterval: 1, axisLabel: { color: p.text2 }, splitLine: { lineStyle: { color: p.grid } } },
          yAxis: { type: "category", inverse: true, data: VERSIONS.map((v) => VERSION_LABEL[v]), axisLabel: { color: p.text } },
          series: [{ type: "bar", data: counts, barMaxWidth: 18, itemStyle: { color: p.series[0], borderRadius: [0, 4, 4, 0] },
            label: { show: true, position: "right", color: p.text2 } }],
        }} />
        <div className="muted">訊號是收盤後才成立的研究訊號；最早只能在下一個交易日開盤模擬進場。<a href="#/signals">看名單 →</a></div>
      </div>
      <div className="muted">最後下載：{data.last_download ?? "—"}。{data.notice}</div>
    </>
  );
}
