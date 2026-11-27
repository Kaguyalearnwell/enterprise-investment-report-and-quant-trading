import { useEffect, useState } from "react";
import { get } from "./api";

export function useApi<T>(path: string | null, params: Record<string, any> = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = path + JSON.stringify(params);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    setData(null); setError(null);
    get<T>(path, params).then((d) => alive && setData(d)).catch((e) => alive && setError(String(e.message ?? e)));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { data, error };
}

export function useHashRoute(): [string, URLSearchParams] {
  const read = () => {
    const h = window.location.hash.replace(/^#/, "") || "/";
    const [p, q] = h.split("?");
    return [p, new URLSearchParams(q ?? "")] as [string, URLSearchParams];
  };
  const [r, setR] = useState(read);
  useEffect(() => {
    const f = () => setR(read());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return r;
}

export function useDark() {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const [dark, setDark] = useState(mq.matches);
  useEffect(() => {
    const f = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener("change", f);
    return () => mq.removeEventListener("change", f);
  }, [mq]);
  return dark;
}
