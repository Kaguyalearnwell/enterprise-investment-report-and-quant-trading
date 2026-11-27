// Thin client for the read-only research API (backend/api/app.py). No write endpoints exist.
export async function get<T>(path: string, params: Record<string, string | number | boolean | undefined> = {}): Promise<T> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const r = await fetch(`/api${path}${q.toString() ? "?" + q : ""}`);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = JSON.stringify((await r.json()).detail); } catch { /* not json */ }
    throw new Error(`${r.status} ${detail}`);
  }
  return r.json();
}

export const exportUrl = (version: string, fmt: "csv" | "xlsx", volume_mode: string) =>
  `/api/signals/export?version=${version}&fmt=${fmt}&volume_mode=${volume_mode}`;

export type Row = Record<string, any>;

export const fmtPct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
export const fmtNum = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : v.toLocaleString("zh-TW", { maximumFractionDigits: d, minimumFractionDigits: 0 });
