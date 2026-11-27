import { useApi } from "../hooks";
import { type Row } from "../api";
import { Loading, Table } from "./common";

export function DataQuality() {
  const { data, error } = useApi<Row>("/data-quality");
  if (!data) return <Loading error={error} />;
  const s = data.survivorship;
  return (
    <>
      <div className="banner">已知限制：{data.limitations.join("；")}</div>
      <div className="two">
        <div className="card"><h2>檢查項目（{data.latest_run}）</h2>
          <Table rows={data.issue_summary} cols={[["check_name", "檢查"], ["category", "類別"],
            ["severity", "嚴重度", (v) => <span className={`badge ${v === "error" ? "fail" : v === "warning" ? "warn" : ""}`}>{v}</span>], ["n", "筆數", undefined, true]]} />
        </div>
        <div className="card"><h2>個股判定</h2>
          <Table rows={data.verdicts} max={380} cols={[["stock_id", "代號", (v) => <a href={`#/stock/${v}`}>{v}</a>],
            ["verdict", "判定", (v) => <span className={`badge ${v}`}>{v}</span>], ["reasons", "原因"]]} />
        </div>
      </div>
      {s && <div className="card"><h2>倖存者偏誤稽核</h2><pre>{JSON.stringify(s, null, 2)}</pre></div>}
      <div className="card"><h2>警告與錯誤明細（前 200 筆）</h2>
        <Table rows={data.issues} max={360} cols={[["stock_id", "代號"], ["date", "日期"], ["check_name", "檢查"],
          ["severity", "嚴重度"], ["detail", "內容"], ["action", "處理"]]} /></div>
      <div className="card"><h2>下載失敗／權限不足</h2>
        <Table rows={data.failed_downloads} cols={[["dataset", "資料集"], ["stock_id", "代號"], ["attempts", "次數", undefined, true],
          ["error_message", "錯誤"], ["updated_at", "時間"]]} /></div>
      <div className="card"><h2>資料來源</h2>
        <Table rows={data.sources} cols={[["source_id", "ID"], ["name", "名稱"], ["provider", "提供者"], ["tier_required", "權限"],
          ["coverage_start", "起始"], ["verification", "驗證"], ["verified_on", "驗證日"]]} /></div>
    </>
  );
}
