import type { ReactNode } from "react";

export const Loading = ({ error }: { error?: string | null }) =>
  error ? <div className="card err">讀取失敗：{error}</div> : <div className="card muted">載入中…</div>;

export const Tile = ({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) => (
  <div className="card tile"><div className="label">{label}</div><div className="value">{value}</div>
    {sub && <div className="sub">{sub}</div>}</div>
);

const show = (v: any) => (v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

export function Table({ rows, cols, max }: { rows: Record<string, any>[];
  cols: [string, string, ((v: any, r: Record<string, any>) => ReactNode)?, boolean?][]; max?: number }) {
  if (!rows.length) return <div className="muted">（沒有資料）</div>;
  return (
    <div className="scroll" style={max ? { maxHeight: max } : undefined}>
      <table>
        <thead><tr>{cols.map(([k, l, , n]) => <th key={k} className={n ? "num" : ""}>{l}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i}>{cols.map(([k, , f, n]) => <td key={k} className={n ? "num" : ""}>{f ? f(r[k], r) : show(r[k])}</td>)}</tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export const VERSIONS = ["A", "B", "C", "D", "E", "S0"] as const;
// Must match backend/strategies/fib_bb_weekly.py VERSIONS (cumulative conditions).
export const VERSION_LABEL: Record<string, string> = {
  A: "A 均線多頭", B: "B ＋近期壓縮", C: "C ＋突破上軌", D: "D ＋成交量確認", E: "E ＋週線（完整策略）", S0: "S0 壓縮突破＋量（無均線）",
};
