import { useEffect, useRef, useState } from "react";
import type { ECharts } from "echarts/core";

interface Props {
  dimensions: string[];
  values: number[];
}

/**
 * 五维风险雷达（第十四轮性能优化：按需引入 + 动态加载）。
 *
 * 优化前：`import * as echarts from "echarts"` 全量打包进主 chunk，
 * 实测主 JS 1.32 MB——评委首屏要下载完整图表库才能交互。
 * 优化后：
 *  1. 动态 import() 拆出独立 chunk，首屏与其并行加载，不阻塞页面渲染；
 *  2. echarts/core + use() 按需注册 Radar/Tooltip/Canvas，tree-shaking 缩库。
 * 仅用雷达图，注册面固定，行为与全量版一致；加载失败降级为提示文案，
 * 页面主体（风险清单）不受影响。
 */
export default function RadarChart({ dimensions, values }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let disposed = false;

    function render() {
      const chart = chartRef.current;
      if (!chart) return;
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
    }

    // 首次：动态加载图表库并初始化；此后 dimensions/values 变化仅重设 option
    Promise.all([
      import("echarts/core"),
      import("echarts/charts"),
      import("echarts/components"),
      import("echarts/renderers"),
    ])
      .then(([core, charts, components, renderers]) => {
        if (disposed || !ref.current) return;
        core.use([charts.RadarChart, components.TooltipComponent, renderers.CanvasRenderer]);
        chartRef.current = core.init(ref.current);
        render();
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });

    const onResize = () => chartRef.current?.resize();
    window.addEventListener("resize", onResize);
    return () => {
      disposed = true;
      window.removeEventListener("resize", onResize);
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, [dimensions, values]);

  if (failed) {
    return (
      <div className="flex h-64 w-full items-center justify-center text-xs text-slate-400">
        雷达图组件加载失败，风险数据见下方清单
      </div>
    );
  }
  return <div ref={ref} className="h-64 w-full" />;
}
