import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export function Chart({ option, height = 360, onClick }: { option: echarts.EChartsOption; height?: number;
  onClick?: (p: any) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!el.current) return;
    inst.current = echarts.init(el.current);
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); inst.current?.dispose(); };
  }, []);
  useEffect(() => { inst.current?.setOption(option, true); }, [option]);
  useEffect(() => {
    const c = inst.current;
    if (!c || !onClick) return;
    c.on("click", onClick);
    return () => { c.off("click", onClick); };
  }, [onClick]);
  return <div ref={el} style={{ width: "100%", height }} />;
}
