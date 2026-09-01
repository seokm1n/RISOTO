import { useEffect, useMemo, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { PanelTitle } from "../../shared/components";
import RiskOverviewTrendChart from "../../shared/RiskOverviewTrendChart";
import {
  RISK_TYPE_LABELS,
  formatDate,
  formatNumber,
  formatPercent,
  riskEventTitle,
  sentimentKind,
  sentimentText,
} from "../../shared/presentation";
import { useSharedResource } from "../../shared/useSharedResource";

const MAIN_TREND_DAYS = 7;
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

// 현재 날짜·시간을 일정 주기로 갱신해서 "최근 수집 기사" 패널 위에 표시한다.
function useNow(intervalMs) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

// 로그인 직후 나의 기업의 현황·통합 위험 추세와 최신 기사·위험 판정을 한 화면에 모은다.
// 기업 목록·대시보드 통계는 다른 화면과 캐시를 공유해 중복 폴링을 하지 않는다.
export default function MainPage({ onOpenCompany }) {
  const { data: companies = [], error: companiesError, loading } = useSharedResource(
    "/companies", () => api.get("/companies").then((response) => response.data),
  );
  const { data: overview } = useSharedResource(
    "/dashboard/overview?days=7", () => api.get("/dashboard/overview?days=7").then((response) => response.data),
  );
  const mainCompany = companies.find((company) => company.company_role === "main");
  const mainId = mainCompany?.id ?? null;

  const { data: dailySummaries = [] } = useSharedResource(
    mainId ? `/companies/${mainId}/daily-summaries?days=${MAIN_TREND_DAYS}` : "skip:main-daily-summaries",
    mainId
      ? () => api.get(`/companies/${mainId}/daily-summaries?days=${MAIN_TREND_DAYS}`).then((response) => response.data)
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
    <p className="main-lead main-page-intro">나의 기업의 수집 현황, 위험 신호와 대응 상태를 한 화면에서 확인합니다.</p>
    <div className="main-page-shell">
    <div className="workspace-head">
      <div className="main-workspace-heading"><span className="eyebrow">MY COMPANY</span><h1>{mainCompany ? <button className="company-name-link main-company-name" type="button" onClick={() => onOpenCompany(mainCompany.id)}>나의 기업 - {mainCompany.name}</button> : "나의 기업"}</h1></div>
    </div>
    {error && <div className="notice error">{error}</div>}
    {loading ? <p className="empty-state">기업 정보를 불러오는 중입니다.</p> : !mainCompany ? <p className="empty-state">나의 기업 정보가 없습니다.</p> : <div className="main-dashboard-grid">
      <section className="panel main-left-panel">
        <div className="metric-grid main-summary-metrics">
          <article className="metric"><span>최근 7일 기사</span><strong>{formatNumber(mainEntry?.article_count ?? 0)}<small className="count-unit">건</small></strong></article>
          <article className="metric"><span>일일 수집</span><strong>{todayCount == null ? "-" : <>{formatNumber(todayCount)}<small className="count-unit">건</small></>}</strong></article>
          <article className="main-metric-tile"><span className="main-metric-tile-label">감성 비율</span><SentimentRatioBar sentiment={mainSentiment} /></article>
          <article className={`metric ${mainEntry?.risk_count ? "danger" : "success"}`}><span>위험 판정</span><strong>{formatNumber(mainEntry?.risk_count ?? 0)}<small className="count-unit">건</small></strong></article>
        </div>
        <div className="main-charts-row">
          <div className="main-chart-col">
            <PanelTitle kicker="최근 7일 · 왼쪽 건수 / 오른쪽 비율" title="수집·위험·부정 기사 추이" />
            <RiskOverviewTrendChart days={dailySummaries} />
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
            {recentArticles.length ? recentArticles.map((article) => <article className="main-article-row" key={article.id}>
              <span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span>
              <div><strong>{article.title}</strong>{article.summary && <p>{truncate(article.summary, SUMMARY_MAX_CHARS)}</p>}</div>
            </article>) : <p className="panel-empty">아직 수집된 기사가 없습니다.</p>}
          </div>
        </section>
        <button type="button" className="panel main-risk-panel main-risk-panel-link" onClick={() => goToDetail(latestRisk?.id)}>
          <PanelTitle kicker="LLM 위험 유형 분류·보고서" title="위험 판정 요약" />
          <div className="main-risk-notice-scroll">
            {latestRisk ? <div className="main-risk-notice">
              <span className={`severity ${latestRisk.severity}`}>{latestRisk.severity === "critical" ? "긴급" : "주의"}</span>
              <p>{(latestRisk.risk_types ?? []).map((item) => RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type).join(", ") || "위험"} 유형의 위험 신호가 감지되어 대응 보고서를 {responseDrafts.length ? "작성했습니다" : "작성 중입니다"}.</p>
              <small><span>{formatDate(latestRisk.detected_at)} · </span><strong className="risk-event-display-title">{riskEventTitle(latestRisk)}</strong></small>
            </div> : <p className="panel-empty">최근 감지된 위험이 없습니다.</p>}
          </div>
        </button>
      </div>
    </div>}
    </div>
  </section>;
}
