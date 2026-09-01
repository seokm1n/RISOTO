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

// 수집량은 왼쪽 건수 축, 위험·부정 기사 비율은 오른쪽 백분율 축에 함께 그린다.
export default function RiskOverviewTrendChart({ days = [], ariaLabel = "수집량, 위험 수집 비율, 부정 기사 비율 추이" }) {
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
        risk_article_ratio: articleCount > 0 ? riskArticleCount / articleCount : null,
        negative_article_ratio: articleCount > 0 ? negativeArticleCount / articleCount : null,
      };
    });
  if (!points.length) return <div className="main-overview-trend">
    <div className="main-overview-legend" aria-hidden="true" />
    <div className="main-chart-canvas" ref={canvasRef}><p className="panel-empty">아직 표시할 수집·위험 데이터가 없습니다.</p></div>
  </div>;

  const width = measuredWidth || 700, height = measuredHeight || 210;
  const left = 42, right = 46, top = 18, bottom = 28;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const maxCollection = Math.max(...points.map((day) => day.article_count ?? 0), 1);
  const x = (index) => left + (points.length === 1 ? plotWidth / 2 : index / (points.length - 1) * plotWidth);
  const yCount = (value) => top + plotHeight - Math.min(value / maxCollection, 1) * plotHeight;
  const yRatio = (value) => top + plotHeight - Math.min(Math.max(value, 0), 1) * plotHeight;
  const formatDay = (value) => new Date(value).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
  const gridLevels = [0, .5, 1];
  const labelEvery = Math.max(1, Math.ceil(points.length / 4));
  const latest = points.at(-1);
  const ratioLabel = (value) => value == null ? "-" : formatPercent(value);
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
      <span className="collection"><i />수집량 <strong>{formatNumber(latest.article_count)}건</strong></span>
      <span className="risk"><i />위험 수집 비율 <strong>{ratioLabel(latest.risk_article_ratio)}</strong></span>
      <span className="negative"><i />부정 기사 비율 <strong>{ratioLabel(latest.negative_article_ratio)}</strong></span>
    </div>
    <div className="main-chart-canvas" ref={canvasRef}>
      <svg className="main-trend-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        {gridLevels.map((level) => <g key={level}>
          <line className="main-trend-grid-line" x1={left} x2={width - right} y1={yRatio(level)} y2={yRatio(level)} />
          <text className="main-trend-axis-label" x={left - 8} y={yRatio(level) + 4} textAnchor="end">{formatNumber(Math.round(maxCollection * level))}</text>
          <text className="main-trend-axis-label" x={width - right + 8} y={yRatio(level) + 4} textAnchor="start">{Math.round(level * 100)}%</text>
        </g>)}
        <text className="main-trend-axis-unit" x={left - 8} y={10} textAnchor="end">건</text>
        <text className="main-trend-axis-unit" x={width - right + 8} y={10} textAnchor="start">비율</text>
        {points.map((day, index) => (index % labelEvery === 0 || index === points.length - 1) && <text className="main-trend-axis-label" key={`date-${day.summary_date}`} x={x(index)} y={height - 6} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}>{formatDay(day.summary_date)}</text>)}
        {segments("article_count", yCount).map((line, index) => <polyline className="main-overview-line collection" points={line} key={`collection-${index}`} />)}
        {segments("risk_article_ratio", yRatio).map((line, index) => <polyline className="main-overview-line risk" points={line} key={`risk-${index}`} />)}
        {segments("negative_article_ratio", yRatio).map((line, index) => <polyline className="main-overview-line negative" points={line} key={`negative-${index}`} />)}
        {points.map((day, index) => <g key={`points-${day.summary_date}`}>
          <circle className="main-overview-point collection" cx={x(index)} cy={yCount(day.article_count)} r="3.2"><title>{`${formatDay(day.summary_date)} · 수집량 ${formatNumber(day.article_count)}건`}</title></circle>
          {day.risk_article_ratio != null && <circle className="main-overview-point risk" cx={x(index)} cy={yRatio(day.risk_article_ratio)} r="3.4"><title>{`${formatDay(day.summary_date)} · 위험 기사 ${formatNumber(day.risk_article_count)}건 / ${formatNumber(day.article_count)}건 (${formatPercent(day.risk_article_ratio)})`}</title></circle>}
          {day.negative_article_ratio != null && <circle className="main-overview-point negative" cx={x(index)} cy={yRatio(day.negative_article_ratio)} r="3.4"><title>{`${formatDay(day.summary_date)} · 부정 기사 ${formatNumber(day.negative_article_count)}건 / ${formatNumber(day.article_count)}건 (${formatPercent(day.negative_article_ratio)})`}</title></circle>}
        </g>)}
      </svg>
    </div>
  </div>;
}
