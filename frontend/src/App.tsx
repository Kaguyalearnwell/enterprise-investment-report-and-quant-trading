import { useHashRoute } from "./hooks";
import { Overview } from "./pages/Overview";
import { Signals } from "./pages/Signals";
import { Stock } from "./pages/Stock";
import { Backtests } from "./pages/Backtests";
import { Experiments } from "./pages/Experiments";
import { DataQuality } from "./pages/DataQuality";

const NAV: [string, string][] = [["/", "總覽"], ["/signals", "每日訊號"], ["/stock", "個股"], ["/backtests", "回測"],
  ["/experiments", "統計驗證"], ["/data", "資料品質"]];

export function App() {
  const [path, q] = useHashRoute();
  const base = "/" + (path.split("/")[1] ?? "");
  let page;
  if (base === "/signals") page = <Signals />;
  else if (base === "/stock") page = <Stock id={path.split("/")[2] ?? q.get("id") ?? "2330"} date={q.get("date")} />;
  else if (base === "/backtests") page = <Backtests id={path.split("/")[2] ?? null} unseal={q.get("unseal") === "1"} />;
  else if (base === "/experiments") page = <Experiments id={path.split("/")[2] ?? null} />;
  else if (base === "/data") page = <DataQuality />;
  else page = <Overview />;
  return (
    <>
      <header>
        <strong>台股量化研究平台</strong>
        <nav>{NAV.map(([h, l]) => <a key={h} href={"#" + h} className={base === h ? "on" : ""}>{l}</a>)}</nav>
      </header>
      <div className="notice">研究與模擬用途。本系統不連接券商、不提供下單或任何交易功能；訊號不是投資建議。</div>
      <main>{page}</main>
    </>
  );
}
