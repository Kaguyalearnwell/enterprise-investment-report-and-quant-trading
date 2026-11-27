import { useState } from "react";
import { useApi } from "../hooks";
import { exportUrl, fmtNum, type Row } from "../api";
import { Loading, Table, VERSIONS, VERSION_LABEL } from "./common";

export function Signals() {
  const [version, setVersion] = useState("E");
  const [volumeMode, setVolumeMode] = useState("include_today");
  const [date, setDate] = useState("");
  const { data, error } = useApi<Row>("/signals", { version, volume_mode: volumeMode, date: date || undefined });
  return (
    <>
      <div className="controls">
        <label>版本 <select value={version} onChange={(e) => setVersion(e.target.value)}>
          {VERSIONS.map((v) => <option key={v} value={v}>{VERSION_LABEL[v]}</option>)}</select></label>
        <label>均量定義 <select value={volumeMode} onChange={(e) => setVolumeMode(e.target.value)}>
          <option value="include_today">含當日（預設）</option><option value="prior_20">前 20 日</option></select></label>
        <label>日期 <input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        {date && <button onClick={() => setDate("")}>最新</button>}
        <a href={exportUrl(version, "csv", volumeMode) + (date ? `&date=${date}` : "")}>匯出 CSV</a>
        <a href={exportUrl(version, "xlsx", volumeMode) + (date ? `&date=${date}` : "")}>匯出 Excel</a>
      </div>
      {!data ? <Loading error={error} /> : (
        <div className="card">
          <h2>{data.date}・{VERSION_LABEL[version]}・{data.rows.length} 檔</h2>
          <Table rows={data.rows} cols={[
            ["stock_id", "代號", (v, r) => <a href={`#/stock/${v}?date=${r.signal_date}`}>{v}</a>],
            ["stock_name", "名稱"], ["industry_category", "產業"], ["exchange", "市場"],
            ["close_raw", "收盤", (v) => fmtNum(v), true],
            ["bbw_pct", "BBW 百分位", (v) => fmtNum(v, 1), true],
            ["volume_ratio_incl", "量比", (v) => fmtNum(v, 2), true],
            ["turnover_ma20", "20 日均成交額（百萬）", (v) => fmtNum(v / 1e6, 0), true],
            ["weekly_trend", "週線", (v) => (v ? "多頭" : "否")],
            ["reference_initial_stop", "參考初始停損", (v) => fmtNum(v), true],
            ["earliest_entry_date", "最早模擬進場"],
          ]} />
          <p className="muted">{data.notice} 參考停損以訊號日收盤計算，實際模擬以下一日開盤價重算。</p>
        </div>
      )}
    </>
  );
}
