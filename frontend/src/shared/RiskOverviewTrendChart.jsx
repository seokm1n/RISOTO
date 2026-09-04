import { useEffect, useRef, useState } from "react";

import { formatNumber, formatPercent } from "./presentation";

// 컨테이너의 실제 렌더 크기를 재서 SVG 좌표와 화면 비율을 일치시킨다.
function useElementSize() {
  const ref = useRef(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize((current) => (current.width === width && current.height === height ? current : { width, height }));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, size];
}

// 기사 2건 이상에 처음 도달한 같은 스토리 코호트 안에서 위험·부정 비율을 비교한다.
export default function RiskOverviewTrendChart({ days = [], ariaLabel = "위험 판정 기사와 부정 기사 비율 추이", displayDates = null }) {
  const [canvasRef, { width: measuredWidth, height: measuredHeight }] = useElementSize();
  const displayDateSet = displayDates === null ? null : new Set(displayDates);
  const points = [...days]
    .sort((left, right) => left.summary_date.localeCompare(right.summary_date))
    .map((day) => {
      const eligibleStoryCount = Math.max(Number(day.eligible_story_count) || 0, 0);
      const riskStoryCount = Math.max(Number(day.eligible_risk_story_count) || 0, 0);
      const negativeStoryCount = Math.max(Number(day.eligible_negative_story_count) || 0, 0);
      return {
        ...day,
        eligible_story_count: eligibleStoryCount,
        risk_story_count: riskStoryCount,
        negative_story_count: negativeStoryCount,
        risk_ratio: eligibleStoryCount > 0 ? Math.min(riskStoryCount / eligibleStoryCount, 1) : 0,
        negative_ratio: eligibleStoryCount > 0 ? Math.min(negativeStoryCount / eligibleStoryCount, 1) : 0,
      };
    })
    .filter((day) => displayDateSet
      ? displayDateSet.has(day.summary_date)
      : day.risk_story_count > 0 || day.negative_story_count > 0);
  if (!points.length) return <div className="main-overview-trend">
    <div className="main-overview-legend" aria-hidden="true" />
    <div className="main-chart-canvas" ref={canvasRef}><p className="panel-empty">아직 표시할 위험·부정 스토리 비율 데이터가 없습니다.</p></div>
  </div>;

  const width = measuredWidth || 700, height = measuredHeight || 210;
  const left = 42, right = 18, top = 18, bottom = 28;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = (index) => left + (points.length === 1 ? plotWidth / 2 : index / (points.length - 1) * plotWidth);
  const yRatio = (value) => top + plotHeight - Math.min(Math.max(value, 0), 1) * plotHeight;
  const formatDay = (value) => new Date(value).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
  const gridLevels = [0, .5, 1];
  const labelEvery = 1;
  const segments = (key, y) => {
    const result = [];
    let current = [];
    points.forEach((point, index) => {
      const value = point[key];
      if (value == null) {
        if (current.length) result.push(current.join(" "));
        current = [];
      } else {
        current.push(`${x(index)},${y(value)}`);
      }
    });
    if (current.length) result.push(current.join(" "));
    return result;
  };

  return <div className="main-overview-trend">
    <div className="main-overview-legend" aria-hidden="true">
      <span className="risk"><i />위험 판정 기사</span>
      <span className="negative"><i />부정 기사</span>
    </div>
    <div className="main-chart-canvas" ref={canvasRef}>
      <svg className="main-trend-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        {gridLevels.map((level) => <g key={level}>
          <line className="main-trend-grid-line" x1={left} x2={width - right} y1={yRatio(level)} y2={yRatio(level)} />
          <text className="main-trend-axis-label" x={left - 8} y={yRatio(level) + 4} textAnchor="end">{formatNumber(level * 100)}%</text>
        </g>)}
        <text className="main-trend-axis-unit" x={left - 8} y={10} textAnchor="end">비율</text>
        {points.map((day, index) => (index % labelEvery === 0 || index === points.length - 1) && <text className="main-trend-axis-label" key={`date-${day.summary_date}`} x={x(index)} y={height - 6} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}>{formatDay(day.summary_date)}</text>)}
        {segments("risk_ratio", yRatio).map((line, index) => <polyline className="main-overview-line risk" points={line} key={`risk-${index}`} />)}
        {segments("negative_ratio", yRatio).map((line, index) => <polyline className="main-overview-line negative" points={line} key={`negative-${index}`} />)}
        {points.map((day, index) => <g key={`points-${day.summary_date}`}>
          <circle className="main-overview-point risk" cx={x(index)} cy={yRatio(day.risk_ratio)} r="3.4"><title>{`${formatDay(day.summary_date)} · 위험 판정 기사 ${formatPercent(day.risk_ratio)} (${formatNumber(day.risk_story_count)}/${formatNumber(day.eligible_story_count)}건)`}</title></circle>
          <circle className="main-overview-point negative" cx={x(index)} cy={yRatio(day.negative_ratio)} r="3.4"><title>{`${formatDay(day.summary_date)} · 부정 기사 ${formatPercent(day.negative_ratio)} (${formatNumber(day.negative_story_count)}/${formatNumber(day.eligible_story_count)}건)`}</title></circle>
        </g>)}
      </svg>
    </div>
  </div>;
}
