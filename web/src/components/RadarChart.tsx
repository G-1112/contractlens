import { useEffect, useRef } from "react";
import * as echarts from "echarts";

interface Props {
  dimensions: string[];
  values: number[];
}

export default function RadarChart({ dimensions, values }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;

    chart.setOption({
      tooltip: {},
      radar: {
        indicator: dimensions.map((name) => ({ name, max: 100 })),
        radius: "65%",
        axisName: { color: "#475569", fontSize: 12 },
        splitArea: { areaStyle: { color: ["#f8fafc", "#ffffff"] } },
      },
      series: [
        {
          type: "radar",
          data: [
            {
              value: values,
              name: "安全分",
              areaStyle: { color: "rgba(37, 99, 235, 0.25)" },
              lineStyle: { color: "#2563eb", width: 2 },
              itemStyle: { color: "#2563eb" },
            },
          ],
        },
      ],
    });

    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, [dimensions, values]);

  return <div ref={ref} className="h-64 w-full" />;
}
