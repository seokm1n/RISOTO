import { useMemo } from "react";

import { api, getErrorMessage } from "../../api";
import { PanelTitle } from "../../shared/components";
import RiskOverviewTrendChart from "../../shared/RiskOverviewTrendChart";
import {
  RISK_TYPE_LABELS,
  formatDate,
  formatNumber,
  formatPercent,
  formatRiskProbability,
  riskEventTitle,
} from "../../shared/presentation";
import { useSharedResource } from "../../shared/useSharedResource";

const MAIN_TREND_DAYS = 7;
const RISK_ARTICLE_LIMIT = 8;

const RESPONSE_STATUS_LABELS = {
  pending: "생성 중",
  generating: "생성 중",
  generated: "생성 완료",
  deferred: "생성 보류",
  failed: "생성 실패",
  idle: "미생성",
};

function averageDailySummaries(groups) {
  if (!groups.length) return [];
  const oneDecimal = (value) => Math.round((value / groups.length) * 10) / 10;
  const byDate = new Map();
  groups.flat().forEach((day) => {
    const current = byDate.get(day.summary_date) ?? {
      summary_date: day.summary_date,
      article_count: 0,
      risk_article_count: 0,
      negative_article_count: 0,
      story_count: 0,
      risk_event_count: 0,
    };
    current.article_count += day.article_count ?? 0;
    current.risk_article_count += day.risk_article_count ?? 0;
    current.negative_article_count += day.negative_article_count ?? 0;
    current.story_count += day.story_count ?? 0;
    current.risk_event_count += day.risk_event_count ?? 0;
    byDate.set(day.summary_date, current);
  });
  return [...byDate.values()]
    .map((day) => ({
      ...day,
      article_count: oneDecimal(day.article_count),
      risk_article_count: oneDecimal(day.risk_article_count),
      negative_article_count: oneDecimal(day.negative_article_count),
      story_count: oneDecimal(day.story_count),
      risk_event_count: oneDecimal(day.risk_event_count),
    }));
}

function CollectionRiskPie({ label, collectionCount, riskCount }) {
  const safeCollectionCount = Math.max(collectionCount, 0);
  const safeRiskCount = Math.min(Math.max(riskCount, 0), safeCollectionCount);
  const nonRiskCount = Math.max(safeCollectionCount - safeRiskCount, 0);
  const riskRatio = safeCollectionCount > 0 ? safeRiskCount / safeCollectionCount : 0;
  const degrees = Math.min(Math.max(riskRatio * 360, 0), 360);
  return <article className="collection-pie-card">
    <h3>{label}</h3>
    <div className="collection-pie" style={{ background: `conic-gradient(#b65232 0deg ${degrees}deg, #e4c88d ${degrees}deg 360deg)` }} role="img" aria-label={`${label} 수집 ${safeCollectionCount}건 중 위험 ${safeRiskCount}건, ${formatPercent(riskRatio)}`}>
      <div><strong>{formatPercent(riskRatio)}</strong><span>위험 / 수집</span></div>
    </div>
    <dl>
      <div><dt><i />전체 수집</dt><dd>{formatNumber(safeCollectionCount)}건</dd></div>
      <div><dt><i className="risk" />위험 판정</dt><dd>{formatNumber(safeRiskCount)}건</dd></div>
      <div><dt><i className="normal" />비위험</dt><dd>{formatNumber(nonRiskCount)}건</dd></div>
    </dl>
  </article>;
}

function CollectionRiskPies({ todayCount, todayRiskCount, sevenDayCount, sevenDayRiskCount }) {
  return <div className="collection-share">
    <CollectionRiskPie label="오늘" collectionCount={todayCount} riskCount={todayRiskCount} />
    <CollectionRiskPie label="최근 7일" collectionCount={sevenDayCount} riskCount={sevenDayRiskCount} />
  </div>;
}

// 로그인 직후 나의 기업과 등록 기업 평균을 비교하고 최신 위험 근거를 브리핑한다.
export default function MainPage({ onOpenCompany }) {
  const { data: companies = [], error: companiesError, loading } = useSharedResource(
    "/companies", () => api.get("/companies").then((response) => response.data),
  );
  const mainCompany = companies.find((company) => company.company_role === "main");
  const mainId = mainCompany?.id ?? null;
  const companyIds = companies.map((company) => company.id).join(",");

  const { data: dailyGroups = [] } = useSharedResource(
    companyIds ? `main-briefing-daily:${companyIds}` : "skip:main-briefing-daily",
    companyIds
      ? () => Promise.all(companies.map((company) => api.get(`/companies/${company.id}/daily-summaries?days=${MAIN_TREND_DAYS}`).then((response) => response.data)))
      : () => Promise.resolve([]),
  );
  const mainIndex = companies.findIndex((company) => company.id === mainId);
  const dailySummaries = mainIndex >= 0 ? dailyGroups[mainIndex] ?? [] : [];
  const mainGraphDates = new Set(
    dailySummaries
      .filter((day) => (day.story_count ?? 0) > 0)
      .map((day) => day.summary_date),
  );
  const averageSummaries = averageDailySummaries(dailyGroups)
    .filter((day) => mainGraphDates.has(day.summary_date));

  const { data: riskPage } = useSharedResource(
    mainId ? `/companies/${mainId}/risk-events/page?view=active&page=1&page_size=10&response=all` : "skip:main-active-risks",
    mainId
      ? () => api.get(`/companies/${mainId}/risk-events/page?view=active&page=1&page_size=10&response=all`).then((response) => response.data)
      : () => Promise.resolve({ items: [] }),
  );
  const activeRisks = riskPage?.items ?? [];
  const riskyArticles = useMemo(() => {
    const seen = new Set();
    return activeRisks.flatMap((risk) => (risk.evidence_articles ?? [])
      .filter((article) => article.evidence_role === "trigger")
      .map((article) => ({ article, risk })))
      .sort((left, right) => new Date(right.article.published_at ?? 0) - new Date(left.article.published_at ?? 0))
      .filter(({ article }) => {
        if (seen.has(article.article_id)) return false;
        seen.add(article.article_id);
        return true;
      })
      .slice(0, RISK_ARTICLE_LIMIT);
  }, [activeRisks]);

  const error = companiesError ? getErrorMessage(companiesError) : null;
  const todayKey = new Date().toLocaleDateString("sv-SE");
  const todaySummary = dailySummaries.find((day) => day.summary_date === todayKey);
  const todayCount = todaySummary?.story_count ?? 0;
  const todayRiskCount = todaySummary?.risk_event_count ?? 0;
  const sevenDayCount = dailySummaries.reduce((sum, day) => sum + (day.story_count ?? 0), 0);
  const sevenDayRiskCount = dailySummaries.reduce((sum, day) => sum + (day.risk_event_count ?? 0), 0);

  return <section className="workspace main-workspace briefing-workspace">
    <p className="main-page-intro briefing-description">실시간으로 수집한 기사를 모델이 분석하고, AI가 위험 여부와 유형을 분류·판단한 결과입니다.</p>
    <div className="main-page-shell briefing-shell">
      {error && <div className="notice error">{error}</div>}
      {loading ? <p className="empty-state">브리핑을 불러오는 중입니다.</p> : !mainCompany ? <p className="empty-state">나의 기업 정보가 없습니다.</p> : <div className="briefing-grid">
        <div className="briefing-trends">
          <section className="panel briefing-trend-panel">
            <PanelTitle title="나의 기업" />
            <RiskOverviewTrendChart days={dailySummaries} ariaLabel={`${mainCompany.name} 최근 7일 위험 판정 기사와 부정 기사 건수`} />
          </section>
          <section className="panel briefing-trend-panel">
            <PanelTitle kicker={`등록 기업 ${formatNumber(companies.length)}곳 기준`} title="전체 평균" />
            <RiskOverviewTrendChart days={averageSummaries} ariaLabel="등록 기업 전체의 최근 7일 평균 위험 판정 기사와 부정 기사 건수" />
          </section>
        </div>
        <div className="briefing-side">
          <section className="panel briefing-volume-panel">
            <PanelTitle kicker="TODAY / LAST 7 DAYS" title="수집·위험 기사 비율" />
            <CollectionRiskPies todayCount={todayCount} todayRiskCount={todayRiskCount} sevenDayCount={sevenDayCount} sevenDayRiskCount={sevenDayRiskCount} />
          </section>
          <section className="panel briefing-risk-articles">
            <div className="briefing-risk-head"><PanelTitle kicker={`위험 판정 기사 · 최대 ${RISK_ARTICLE_LIMIT}건`} title="위험 수집 기사" /><span>{formatNumber(riskyArticles.length)}건</span></div>
            <div className="briefing-risk-list">{riskyArticles.length ? riskyArticles.map(({ article, risk }) => <article key={article.article_id}>
              <div className="briefing-risk-meta"><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><span>{RISK_TYPE_LABELS[risk.primary_type] ?? risk.primary_type ?? "위험"}</span><span className={`response-status ${risk.response_generation_status}`}>{RESPONSE_STATUS_LABELS[risk.response_generation_status] ?? "미생성"}</span></div>
              <a href={article.url} target="_blank" rel="noreferrer">{article.title}</a>
              <p>{riskEventTitle(risk)}</p>
              <footer><small>{article.source_domain || article.source || "출처 미상"} · {formatDate(article.published_at)} · 위험도 {formatRiskProbability(article.risk_probability)}</small><button type="button" onClick={() => onOpenCompany(mainId, risk.id)}>사건 보기</button></footer>
            </article>) : <p className="panel-empty">현재 활성 사건에 연결된 위험 판정 기사가 없습니다.</p>}</div>
          </section>
        </div>
      </div>}
    </div>
  </section>;
}
