import { useApi } from "../hooks";
import { type Row } from "../api";
import { Loading, Table } from "./common";

export function Experiments({ id }: { id: string | null }) {
  const list = useApi<Row[]>("/experiments");
  const one = useApi<Row>(id ? `/experiments/${id}` : null);
  if (id) {
    if (!one.data) return <Loading error={one.error} />;
    const e = one.data.experiment;
    const segs = [...new Set(one.data.trials.map((t: Row) => t.segment))] as string[];
    // trial metrics are {segment: {metric: value}}; show the headline metrics per segment side by side
    const HEAD = ["trades", "expectancy_r", "sharpe", "max_drawdown", "profit_factor"];
    const msegs = [...new Set(one.data.trials.flatMap((t: Row) => Object.keys(t.metrics ?? {})))] as string[];
    const keys = msegs.flatMap((s) => HEAD.map((h) => `${s}.${h}`));
    const flat = (t: Row) => Object.fromEntries(keys.map((k) => { const [s, h] = k.split("."); return [k, t.metrics?.[s]?.[h]]; }));
    return (
      <>
        <div className="card"><h2>{e.name}（{e.kind}）</h2>
          <div>假設：{e.hypothesis}</div>
          <div className="muted">區段 {e.segment}・策略版本 {e.strategy_version_id}・資料版本 {e.data_version_id}・預先登記 {e.preregistration}・{e.status}</div>
          <h2 style={{ marginTop: 12 }}>設計</h2><pre>{JSON.stringify(e.design, null, 2)}</pre>
        </div>
        <div className="card"><h2>摘要</h2><pre>{JSON.stringify(e.result_summary, null, 2)}</pre></div>
        <div className="card"><h2>試驗（{one.data.trials.length}；區段：{segs.join("、")}）</h2>
          <Table rows={one.data.trials.map((t: Row) => ({ ...t, ...flat(t) }))} max={480}
            cols={[["trial_no", "#"], ["label", "標籤"], ["segment", "區段"],
              ...keys.map((k) => [k, k, (v: any) => (typeof v === "number" ? v.toFixed(4) : v ?? "—"), true] as [string, string, any, boolean])]} />
        </div>
      </>
    );
  }
  if (!list.data) return <Loading error={list.error} />;
  return (
    <div className="card"><h2>實驗登記（全部只用 In-Sample＋Validation；OOS 封存）</h2>
      <Table rows={list.data} cols={[["experiment_id", "實驗", (v) => <a href={`#/experiments/${v}`}>{v}</a>],
        ["kind", "類型"], ["hypothesis", "假設"], ["segment", "區段"], ["status", "狀態"], ["created_at", "建立"]]} />
      <p className="muted">完整解讀見 docs/stage5_report.md。</p>
    </div>
  );
}
