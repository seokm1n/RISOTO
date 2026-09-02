import { useEffect, useRef, useState } from "react";

import { formatNumber } from "./presentation";

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

// 수집이 있었던 날짜의 위험·부정 기사 건수를 한 축에서 비교한다.
export default function RiskOverviewTrendChart({ days = [], ariaLabel = "위험 판정 기사와 부정 기사 건수 추이", hideEmptySignalDays = false }) {
  const [canvasRef, { width: measuredWidth, height: measuredHeight }] = useElementSize();
  const points = [...days]
    .sort((left, right) => left.summary_date.localeCompare(right.summary_date))
    .map((day) => {
      const articleCount = Math.max(Number(day.article_count) || 0, 0);
      const riskArticleCount = Math.max(Number(day.risk_article_count) || 0, 0);
      const negativeArticleCount = Math.max(Number(day.negative_article_count) || 0, 0);
      return {
        ...day,
        article_count: articleCount,
        risk_article_count: riskArticleCount,
        negative_article_count: negativeArticleCount,
      };
    })
    .filter((day) => day.article_count > 0)
    .filter((day) => !hideEmptySignalDays || day.risk_article_count > 0 || day.negative_article_count > 0);
  if (!points.length) return <div className="main-overview-trend">
    <div className="main-overview-legend" aria-hidden="true" />
    <div className="main-chart-canvas" ref={canvasRef}><p className="panel-empty">아직 표시할 위험·부정 기사 데이터가 없습니다.</p></div>
  </div>;

  const width = measuredWidth || 700, height = measuredHeight || 210;
  const left = 42, right = 18, top = 18, bottom = 28;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const maxCount = Math.max(...points.flatMap((day) => [day.risk_article_count, day.negative_article_count]), 1);
  const x = (index) => left + (points.length === 1 ? plotWidth / 2 : index / (points.length - 1) * plotWidth);
  const yCount = (value) => top + plotHeight - Math.min(Math.max(value / maxCount, 0), 1) * plotHeight;
  const formatDay = (value) => new Date(value).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
  const gridLevels = [0, .5, 1];
  const labelEvery = 1;
  const totals = points.reduce((result, day) => ({
    article_count: result.article_count + day.article_count,
    risk_article_count: result.risk_article_count + day.risk_article_count,
    negative_article_count: result.negative_article_count + day.negative_article_count,
  }), { article_count: 0, risk_article_count: 0, negative_article_count: 0 });
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
      <span className="risk"><i />기간 위험 판정 기사 <strong>{formatNumber(totals.risk_article_count)}건</strong></span>
      <span className="negative"><i />기간 부정 기사 <strong>{formatNumber(totals.negative_article_count)}건</strong></span>
    </div>
    <div className="main-chart-canvas" ref={canvasRef}>
      <svg className="main-trend-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        {gridLevels.map((level) => <g key={level}>
          <line className="main-trend-grid-line" x1={left} x2={width - right} y1={yCount(maxCount * level)} y2={yCount(maxCount * level)} />
          <text className="main-trend-axis-label" x={left - 8} y={yCount(maxCount * level) + 4} textAnchor="end">{formatNumber(Math.round(maxCount * level))}</text>
        </g>)}
        <text className="main-trend-axis-unit" x={left - 8} y={10} textAnchor="end">건</text>
        {points.map((day, index) => (index % labelEvery === 0 || index === points.length - 1) && <text className="main-trend-axis-label" key={`date-${day.summary_date}`} x={x(index)} y={height - 6} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}>{formatDay(day.summary_date)}</text>)}
        {segments("risk_article_count", yCount).map((line, index) => <polyline className="main-overview-line risk" points={line} key={`risk-${index}`} />)}
        {segments("negative_article_count", yCount).map((line, index) => <polyline className="main-overview-line negative" points={line} key={`negative-${index}`} />)}
        {points.map((day, index) => <g key={`points-${day.summary_date}`}>
          <circle className="main-overview-point risk" cx={x(index)} cy={yCount(day.risk_article_count)} r="3.4"><title>{`${formatDay(day.summary_date)} · 위험 판정 기사 ${formatNumber(day.risk_article_count)}건`}</title></circle>
          <circle className="main-overview-point negative" cx={x(index)} cy={yCount(day.negative_article_count)} r="3.4"><title>{`${formatDay(day.summary_date)} · 부정 기사 ${formatNumber(day.negative_article_count)}건`}</title></circle>
        </g>)}
      </svg>
    </div>
  </div>;
}
