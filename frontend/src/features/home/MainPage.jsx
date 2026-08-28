import { useEffect, useMemo, useRef, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { PanelTitle } from "../../shared/components";
import {
  RISK_TYPE_LABELS,
  formatDate,
  formatNumber,
  formatPercent,
  sentimentKind,
  sentimentText,
} from "../../shared/presentation";
import { useSharedResource } from "../../shared/useSharedResource";

const TREND_DAYS = 14;
const SUMMARY_MAX_CHARS = 64;

// 요약 문단 길이를 고정해 기사 카드 높이가 내용에 따라 들쭉날쭉해지지 않게 한다.
function truncate(text, maxChars) {
  if (!text || text.length <= maxChars) return text;
  return `${text.slice(0, maxChars).trimEnd()}…`;
}

function averageSentiment(days) {
  const withData = days.filter((day) => day.positive_probability != null);
  if (!withData.length) return null;
  const average = (key) => withData.reduce((sum, day) => sum + (day[key] ?? 0), 0) / withData.length;
  return { positive: average("positive_probability"), neutral: average("neutral_probability"), negative: average("negative_probability") };
}

// 경쟁사별 일별 부정 확률을 날짜 기준으로 모아 하루 평균 한 줄로 합친다.
function mergeCompetitorDailyAverages(perCompetitorLists) {
  const byDate = new Map();
  perCompetitorLists.forEach((list) => {
    list.forEach((day) => {
      if (day.negative_probability == null) return;
      const entry = byDate.get(day.summary_date) ?? { sum: 0, count: 0 };
      entry.sum += day.negative_probability;
      entry.count += 1;
      byDate.set(day.summary_date, entry);
    });
  });
  return Array.from(byDate.entries())
    .map(([summary_date, { sum, count }]) => ({ summary_date, negative_probability: sum / count }))
    .sort((a, b) => a.summary_date.localeCompare(b.summary_date));
}

// 긍정/중립/부정 비율을 한 줄 막대로 보여준다.
function SentimentRatioBar({ sentiment }) {
  if (!sentiment) return <p className="panel-empty">감성 데이터가 없습니다.</p>;
  return <div className="sentiment-ratio">
    <div className="sentiment-ratio-track">
      <span className="positive" style={{ width: `${sentiment.positive * 100}%` }} />
      <span className="neutral" style={{ width: `${sentiment.neutral * 100}%` }} />
      <span className="negative" style={{ width: `${sentiment.negative * 100}%` }} />
    </div>
    <div className="sentiment-ratio-legend">
      <span className="positive">긍정 {formatPercent(sentiment.positive)}</span>
      <span className="neutral">중립 {formatPercent(sentiment.neutral)}</span>
      <span className="negative">부정 {formatPercent(sentiment.negative)}</span>
    </div>
  </div>;
}

// 컨테이너의 실제 렌더 크기를 재서, 그 크기 그대로 좌표를 그리게 한다. viewBox를 고정값으로 두고
// CSS로 늘리면(특히 세로로 긴 박스에서) 원·글자·선 굵기가 세로로만 찌그러지기 때문이다.
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

// 현재 날짜·시간을 일정 주기로 갱신해서 "최근 수집 기사" 패널 위에 표시한다.
function useNow(intervalMs) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

// 일별 위험 판정 건수를 막대로 보여준다. 대부분 0~1건이라 선그래프보다 막대가 더 잘 읽힌다.
function RiskLevelChart({ days }) {
  const [canvasRef, { width: measuredWidth, height: measuredHeight }] = useElementSize();
  const ascending = [...days].sort((a, b) => a.summary_date.localeCompare(b.summary_date));
  if (!ascending.length) return <div className="main-chart-canvas" ref={canvasRef}><p className="panel-empty">아직 표시할 위험 데이터가 없습니다.</p></div>;
  const width = measuredWidth || 320, height = measuredHeight || 172, left = 26, right = 10, top = 18, bottom = 24;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const maxCount = Math.max(...ascending.map((day) => day.risk_event_count), 1);
  const slot = plotWidth / ascending.length;
  const barWidth = Math.max(5, slot * 0.55);
  const y = (value) => top + plotHeight - (value / maxCount) * plotHeight;
  const labelEvery = Math.max(1, Math.ceil(ascending.length / 4));

  return <div className="main-chart-canvas" ref={canvasRef}>
    <svg className="main-trend-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="일별 위험 판정 건수">
      <line className="main-trend-grid-line" x1={left} x2={width - right} y1={y(0)} y2={y(0)} />
      <text className="main-trend-axis-label" x={left - 6} y={top + 4} textAnchor="end">{maxCount}</text>
      <text className="main-trend-axis-label" x={left - 6} y={y(0) + 4} textAnchor="end">0</text>
      {ascending.map((day, index) => {
        const cx = left + slot * (index + 0.5);
        const barY = y(day.risk_event_count);
        return <g key={day.summary_date}>
          <rect className={day.risk_event_count > 0 ? "main-risk-bar-active" : "main-risk-bar"} x={cx - barWidth / 2} y={barY} width={barWidth} height={Math.max(1.5, y(0) - barY)} rx="2" />
          {day.risk_event_count > 0 && <text className="main-trend-bar-label" x={cx} y={barY - 5} textAnchor="middle">{day.risk_event_count}</text>}
          {(index % labelEvery === 0 || index === ascending.length - 1) && <text className="main-trend-axis-label" x={cx} y={height - 6} textAnchor="middle">{new Date(day.summary_date).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}</text>}
        </g>;
      })}
    </svg>
  </div>;
}

// 우리 기업과 경쟁사 평균의 일별 부정 기사 비율을 한 그래프에 색으로 구분해 겹쳐 보여준다.
// 위험이 감지된 날은 우리 기업 선 위에 큰 점으로 강조하고, 두 선의 최신값은 라벨로 표시한다.
function ComparisonTrendChart({ mainDays, competitorDays }) {
  const [canvasRef, { width: measuredWidth, height: measuredHeight }] = useElementSize();
  const mainAscending = [...mainDays]
    .filter((day) => day.negative_probability != null)
    .sort((a, b) => a.summary_date.localeCompare(b.summary_date));
  if (!mainAscending.length) return <div className="main-trend-chart-wrap">
    <div className="main-trend-legend">
      <span className="main-trend-legend-item main"><i />우리 기업</span>
      <span className="main-trend-legend-item competitor"><i />경쟁사 평균</span>
    </div>
    <div className="main-chart-canvas" ref={canvasRef}><p className="panel-empty">아직 표시할 추세 데이터가 없습니다.</p></div>
  </div>;
  const competitorByDate = new Map(competitorDays.map((day) => [day.summary_date, day.negative_probability]));
  const competitorAscending = [...competitorDays].sort((a, b) => a.summary_date.localeCompare(b.summary_date));

  const width = measuredWidth || 380, height = measuredHeight || 172, left = 34, right = 14, top = 18, bottom = 24;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = (index) => left + (mainAscending.length === 1 ? plotWidth / 2 : (index / (mainAscending.length - 1)) * plotWidth);
  const y = (ratio) => top + plotHeight - Math.min(ratio, 1) * plotHeight;

  const mainPoints = mainAscending.map((day, index) => `${x(index)},${y(day.negative_probability)}`).join(" ");
  const competitorPoints = mainAscending
    .map((day, index) => (competitorByDate.has(day.summary_date) ? `${x(index)},${y(competitorByDate.get(day.summary_date))}` : null))
    .filter(Boolean)
    .join(" ");

  const last = mainAscending[mainAscending.length - 1];
  const lastCompetitorValue = competitorByDate.get(last.summary_date)
    ?? (competitorAscending.length ? competitorAscending[competitorAscending.length - 1].negative_probability : null);
  const gridRatios = [0, .5, 1];
  const labelEvery = Math.max(1, Math.ceil(mainAscending.length / 4));

  return <div className="main-trend-chart-wrap">
    <div className="main-trend-legend">
      <span className="main-trend-legend-item main"><i />우리 기업</span>
      <span className="main-trend-legend-item competitor"><i />경쟁사 평균</span>
    </div>
    <div className="main-chart-canvas" ref={canvasRef}>
      <svg className="main-trend-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="일별 부정 기사 비율, 우리 기업과 경쟁사 평균 비교, 위험 감지일은 점으로 표시">
        {gridRatios.map((ratio) => <g key={ratio}>
          <line className="main-trend-grid-line" x1={left} x2={width - right} y1={y(ratio)} y2={y(ratio)} />
          <text className="main-trend-axis-label" x={left - 8} y={y(ratio) + 4} textAnchor="end">{Math.round(ratio * 100)}%</text>
        </g>)}
        {mainAscending.map((day, index) => (index % labelEvery === 0 || index === mainAscending.length - 1) && <text className="main-trend-axis-label" key={`x-${day.summary_date}`} x={x(index)} y={height - 6} textAnchor="middle">{new Date(day.summary_date).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}</text>)}
        {competitorPoints && <polyline className="main-trend-line-competitor" points={competitorPoints} />}
        <polyline className="main-trend-line-main" points={mainPoints} />
        {mainAscending.map((day, index) => <circle key={day.summary_date} className={day.risk_event_count > 0 ? "main-trend-dot-risk" : "main-trend-dot-main"} cx={x(index)} cy={y(day.negative_probability)} r={day.risk_event_count > 0 ? 5.5 : 3} />)}
        <text className="main-trend-label" x={x(mainAscending.length - 1)} y={Math.max(top - 4, y(last.negative_probability) - 12)} textAnchor="end">우리 {formatPercent(last.negative_probability)}</text>
        {lastCompetitorValue != null && <text className="main-trend-label-competitor" x={x(mainAscending.length - 1)} y={Math.min(height - bottom - 2, y(lastCompetitorValue) + 16)} textAnchor="end">경쟁사 {formatPercent(lastCompetitorValue)}</text>}
      </svg>
    </div>
  </div>;
}

// 로그인 직후 우리 기업의 현황·추세·경쟁사 비교와 최신 기사·위험 판정을 한 화면에 모은다.
// 기업 목록·대시보드 통계는 다른 화면과 캐시를 공유해 중복 폴링을 하지 않는다.
export default function MainPage({ onOpenCompany, onEditCompany }) {
  const { data: companies = [], error: companiesError, loading } = useSharedResource(
    "/companies", () => api.get("/companies").then((response) => response.data),
  );
  const { data: overview } = useSharedResource(
    "/dashboard/overview?days=7", () => api.get("/dashboard/overview?days=7").then((response) => response.data),
  );
  const mainCompany = companies.find((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");
  const mainId = mainCompany?.id ?? null;
  const competitorIds = competitorCompanies.map((company) => company.id).join(",");

  const { data: dailySummaries = [] } = useSharedResource(
    mainId ? `/companies/${mainId}/daily-summaries?days=${TREND_DAYS}` : "skip:main-daily-summaries",
    mainId
      ? () => api.get(`/companies/${mainId}/daily-summaries?days=${TREND_DAYS}`).then((response) => response.data)
      : () => Promise.resolve([]),
  );
  const { data: competitorDaily = [] } = useSharedResource(
    competitorIds ? `competitor-daily-avg:${competitorIds}:${TREND_DAYS}` : "skip:competitor-daily-avg",
    competitorIds
      ? async () => {
        const results = await Promise.allSettled(
          competitorCompanies.map((company) => api.get(`/companies/${company.id}/daily-summaries?days=${TREND_DAYS}`)),
        );
        return mergeCompetitorDailyAverages(results.flatMap((result) => result.status === "fulfilled" ? [result.value.data] : []));
      }
      : () => Promise.resolve([]),
  );
  const { data: recentArticles = [] } = useSharedResource(
    mainId ? `/companies/${mainId}/articles?page=1&page_size=5` : "skip:main-recent-articles",
    mainId
      ? () => api.get(`/companies/${mainId}/articles?page=1&page_size=5`).then((response) => response.data.items)
      : () => Promise.resolve([]),
  );
  const { data: latestRisks = [] } = useSharedResource(
    mainId ? `/companies/${mainId}/risk-events?limit=1` : "skip:main-latest-risk",
    mainId
      ? () => api.get(`/companies/${mainId}/risk-events?limit=1`).then((response) => response.data)
      : () => Promise.resolve([]),
  );
  const latestRisk = latestRisks[0] ?? null;
  const { data: responseDrafts = [] } = useSharedResource(
    latestRisk ? `/risk-events/${latestRisk.id}/response-drafts` : "skip:main-response-drafts",
    latestRisk
      ? () => api.get(`/risk-events/${latestRisk.id}/response-drafts`).then((response) => response.data)
      : () => Promise.resolve([]),
  );

  const error = companiesError ? getErrorMessage(companiesError) : null;
  const mainEntry = overview?.companies?.find((company) => company.id === mainId);
  const todayKey = new Date().toLocaleDateString("sv-SE");
  const todayCount = dailySummaries.find((day) => day.summary_date === todayKey)?.article_count ?? null;
  const mainSentiment = useMemo(() => averageSentiment(dailySummaries), [dailySummaries]);
  const now = useNow(30000);
  const nowLabel = now.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

  const goToDetail = (riskEventId) => mainId && onOpenCompany(mainId, riskEventId ?? null);

  return <section className="workspace main-workspace">
    <div className="workspace-head">
      <div><span className="eyebrow">MAIN COMPANY</span><h1>{mainCompany ? <button className="company-name-link main-company-name" type="button" onClick={() => onEditCompany(mainCompany.id)}>{mainCompany.name}</button> : "메인 기업"}</h1><p className="main-lead">우리 기업의 수집 현황, 위험 신호와 대응 상태를 한 화면에서 확인합니다.</p></div>
      {mainCompany && <span className="main-live-collecting"><i className="main-live-spinner" aria-hidden="true" />실시간 수집중</span>}
    </div>
    {error && <div className="notice error">{error}</div>}
    {loading ? <p className="empty-state">기업 정보를 불러오는 중입니다.</p> : !mainCompany ? <p className="empty-state">메인 기업 정보가 없습니다.</p> : <div className="main-dashboard-grid">
      <section className="panel main-left-panel">
        <div className="metric-grid main-summary-metrics">
          <button type="button" className="main-metric-link" onClick={() => goToDetail()}><article className="metric"><span>최근 7일 기사</span><strong>{formatNumber(mainEntry?.article_count ?? 0)}</strong></article></button>
          <button type="button" className="main-metric-link" onClick={() => goToDetail()}><article className="metric"><span>일일 수집</span><strong>{todayCount == null ? "-" : formatNumber(todayCount)}</strong></article></button>
          <button type="button" className="main-metric-tile" onClick={() => goToDetail()}><span className="main-metric-tile-label">감성 비율</span><SentimentRatioBar sentiment={mainSentiment} /></button>
          <button type="button" className="main-metric-link" onClick={() => goToDetail(latestRisk?.id)}><article className={`metric ${mainEntry?.risk_count ? "danger" : "success"}`}><span>위험 판정</span><strong>{formatNumber(mainEntry?.risk_count ?? 0)}</strong></article></button>
        </div>
        <div className="main-charts-row">
          <div className="main-chart-col">
            <PanelTitle kicker="최근 14일" title="위험 판정 추이" />
            <RiskLevelChart days={dailySummaries} />
          </div>
          <div className="main-chart-col">
            <PanelTitle kicker="부정 기사 비율 (%)" title="경쟁사 비교" />
            <ComparisonTrendChart mainDays={dailySummaries} competitorDays={competitorDaily} />
          </div>
        </div>
      </section>
      <div className="main-right-column">
        <section className="panel main-articles-panel">
          <div className="main-articles-panel-head">
            <PanelTitle kicker="최대 5건" title="최근 수집 기사" />
            <time className="main-live-clock" dateTime={now.toISOString()}>{nowLabel}</time>
          </div>
          <div className="main-article-list">
            {recentArticles.length ? recentArticles.map((article) => <a className="main-article-row" href={article.url} target="_blank" rel="noreferrer" key={article.id}>
              <span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span>
              <div><strong>{article.title}</strong>{article.summary && <p>{truncate(article.summary, SUMMARY_MAX_CHARS)}</p>}</div>
            </a>) : <p className="panel-empty">아직 수집된 기사가 없습니다.</p>}
          </div>
        </section>
        <button type="button" className="panel main-risk-panel main-risk-panel-link" onClick={() => goToDetail(latestRisk?.id)}>
          <PanelTitle kicker="LLM 위험 유형 분류·보고서" title="위험 판정 요약" />
          <div className="main-risk-notice-scroll">
            {latestRisk ? <div className="main-risk-notice">
              <span className={`severity ${latestRisk.severity}`}>{latestRisk.severity === "critical" ? "긴급" : "주의"}</span>
              <p>{(latestRisk.risk_types ?? []).map((item) => RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type).join(", ") || "위험"} 유형의 위험 신호가 감지되어 대응 보고서를 {responseDrafts.length ? "작성했습니다" : "작성 중입니다"}.</p>
              <small>{formatDate(latestRisk.detected_at)} · {latestRisk.summary || latestRisk.article_title}</small>
            </div> : <p className="panel-empty">최근 감지된 위험이 없습니다.</p>}
          </div>
        </button>
      </div>
    </div>}
  </section>;
}
