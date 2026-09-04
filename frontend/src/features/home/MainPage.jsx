import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

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
import { resolveSelectedCompany, setSelectedCompanyId as rememberSelectedCompanyId } from "../../shared/selectedCompanySession";

const MAIN_TREND_DAYS = 7;
const RISK_ARTICLE_LIMIT = 5;

const riskActivityDate = (risk) => risk.status === "closed"
  ? risk.closed_at ?? risk.last_evidence_at ?? risk.opened_at
  : risk.last_evidence_at ?? risk.last_seen_at ?? risk.opened_at;

const riskActivityTime = (risk) => {
  const timestamp = new Date(riskActivityDate(risk) ?? 0).getTime();
  return Number.isFinite(timestamp) ? timestamp : 0;
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
      negative_story_count: 0,
      eligible_story_count: 0,
      eligible_positive_story_count: 0,
      eligible_neutral_story_count: 0,
      eligible_negative_story_count: 0,
      eligible_risk_story_count: 0,
      story_count: 0,
      risk_event_count: 0,
    };
    current.article_count += day.article_count ?? 0;
    current.risk_article_count += day.risk_article_count ?? 0;
    current.negative_article_count += day.negative_article_count ?? 0;
    current.negative_story_count += day.negative_story_count ?? 0;
    current.eligible_story_count += day.eligible_story_count ?? 0;
    current.eligible_positive_story_count += day.eligible_positive_story_count ?? 0;
    current.eligible_neutral_story_count += day.eligible_neutral_story_count ?? 0;
    current.eligible_negative_story_count += day.eligible_negative_story_count ?? 0;
    current.eligible_risk_story_count += day.eligible_risk_story_count ?? 0;
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
      negative_story_count: oneDecimal(day.negative_story_count),
      eligible_story_count: oneDecimal(day.eligible_story_count),
      eligible_positive_story_count: oneDecimal(day.eligible_positive_story_count),
      eligible_neutral_story_count: oneDecimal(day.eligible_neutral_story_count),
      eligible_negative_story_count: oneDecimal(day.eligible_negative_story_count),
      eligible_risk_story_count: oneDecimal(day.eligible_risk_story_count),
      story_count: oneDecimal(day.story_count),
      risk_event_count: oneDecimal(day.risk_event_count),
    }));
}

function RiskRatioCard({ periodLabel, eligibleCount, riskCount }) {
  const safeEligibleCount = Math.max(Number(eligibleCount) || 0, 0);
  const safeRiskCount = Math.min(Math.max(Number(riskCount) || 0, 0), safeEligibleCount);
  const nonRiskCount = Math.max(safeEligibleCount - safeRiskCount, 0);
  const riskRatio = safeEligibleCount > 0 ? safeRiskCount / safeEligibleCount : 0;
  const nonRiskRatio = safeEligibleCount > 0 ? nonRiskCount / safeEligibleCount : 0;
  const degrees = Math.min(Math.max(riskRatio * 360, 0), 360);
  const background = safeEligibleCount > 0
    ? `conic-gradient(#b65232 0deg ${degrees}deg, #e4c88d ${degrees}deg 360deg)`
    : "#eee6d8";
  return <article className="briefing-ratio-card">
    <div className="collection-pie" style={{ background }} role="img" aria-label={`${periodLabel} 판정 가능 스토리 ${safeEligibleCount}건 중 위험 ${safeRiskCount}건, ${formatPercent(riskRatio)}`}>
      <div aria-hidden="true" />
    </div>
    <dl>
      <div className="risk"><dt><i className="risk" />위험</dt><dd>{formatPercent(riskRatio)} · {formatNumber(safeRiskCount)}건</dd></div>
      <div className="normal"><dt><i className="normal" />비위험</dt><dd>{formatPercent(nonRiskRatio)} · {formatNumber(nonRiskCount)}건</dd></div>
    </dl>
  </article>;
}

function SentimentRatioCard({ periodLabel, positiveCount, negativeCount, neutralCount }) {
  const positive = Math.max(Number(positiveCount) || 0, 0);
  const negative = Math.max(Number(negativeCount) || 0, 0);
  const neutral = Math.max(Number(neutralCount) || 0, 0);
  const total = positive + negative + neutral;
  const positiveRatio = total > 0 ? positive / total : 0;
  const negativeRatio = total > 0 ? negative / total : 0;
  const neutralRatio = total > 0 ? neutral / total : 0;
  const positiveEnd = positiveRatio * 360;
  const negativeEnd = positiveEnd + negativeRatio * 360;
  const background = total > 0
    ? `conic-gradient(#4f8b66 0deg ${positiveEnd}deg, #b65232 ${positiveEnd}deg ${negativeEnd}deg, #e4c88d ${negativeEnd}deg 360deg)`
    : "#eee6d8";
  return <article className="briefing-ratio-card">
    <div className="collection-pie" style={{ background }} role="img" aria-label={`${periodLabel} 감성 판정 스토리 ${total}건 중 긍정 ${formatPercent(positiveRatio)}, 부정 ${formatPercent(negativeRatio)}, 중립 ${formatPercent(neutralRatio)}`}>
      <div aria-hidden="true" />
    </div>
    <dl>
      <div className="positive"><dt><i className="positive" />긍정</dt><dd>{formatPercent(positiveRatio)} · {formatNumber(positive)}건</dd></div>
      <div className="negative"><dt><i className="negative" />부정</dt><dd>{formatPercent(negativeRatio)} · {formatNumber(negative)}건</dd></div>
      <div className="neutral"><dt><i className="neutral" />중립</dt><dd>{formatPercent(neutralRatio)} · {formatNumber(neutral)}건</dd></div>
    </dl>
  </article>;
}

// 로그인 직후 나의 기업과 등록 기업 평균을 비교하고 최신 위험 근거를 브리핑한다.
export default function MainPage({ onOpenCompany, onOpenRiskPage, onOpenResponseHistory }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [ratioView, setRatioView] = useState("risk");
  const [ratioPeriod, setRatioPeriod] = useState("sevenDays");
  const { data: companies = [], error: companiesError, loading } = useSharedResource(
    "/companies", () => api.get("/companies").then((response) => response.data),
  );
  const mainCompany = companies.find((company) => company.company_role === "main");
  const requestedCompanyId = searchParams.get("companyId") ?? "";
  const selectedCompany = resolveSelectedCompany(companies, requestedCompanyId);
  const selectedCompanyId = selectedCompany?.id ?? null;
  const companyIds = companies.map((company) => company.id).join(",");
  const mainCompanies = companies.filter((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");

  useEffect(() => {
    if (!selectedCompanyId) return;
    rememberSelectedCompanyId(selectedCompanyId);
    if (requestedCompanyId === String(selectedCompanyId)) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("companyId", String(selectedCompanyId));
      return next;
    }, { replace: true });
  }, [requestedCompanyId, selectedCompanyId, setSearchParams]);

  const selectCompany = (companyId) => {
    rememberSelectedCompanyId(companyId);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("companyId", companyId);
      return next;
    });
  };

  const { data: dailyGroups = [] } = useSharedResource(
    companyIds ? `main-briefing-daily:${companyIds}` : "skip:main-briefing-daily",
    companyIds
      ? () => Promise.all(companies.map((company) => api.get(`/companies/${company.id}/daily-summaries?days=${MAIN_TREND_DAYS}`).then((response) => response.data)))
      : () => Promise.resolve([]),
  );
  const selectedIndex = companies.findIndex((company) => company.id === selectedCompanyId);
  const dailySummaries = selectedIndex >= 0 ? dailyGroups[selectedIndex] ?? [] : [];
  const averageSummaries = averageDailySummaries(dailyGroups);
  const trendDisplayDates = dailySummaries
    .filter((day) => (day.eligible_risk_story_count ?? 0) > 0 || (day.eligible_negative_story_count ?? 0) > 0)
    .map((day) => day.summary_date);

  const { data: riskPages = [] } = useSharedResource(
    selectedCompanyId ? `main-briefing-risks:${selectedCompanyId}` : "skip:main-briefing-risks",
    selectedCompanyId
      ? () => Promise.all([
          api.get(`/companies/${selectedCompanyId}/risk-events/page?view=active&page=1&page_size=${RISK_ARTICLE_LIMIT}&response=all`).then((response) => response.data),
          api.get(`/companies/${selectedCompanyId}/risk-events/page?view=history&page=1&page_size=${RISK_ARTICLE_LIMIT}&response=all`).then((response) => response.data),
        ])
      : () => Promise.resolve([]),
  );
  const riskyStories = useMemo(() => riskPages
      .flatMap((riskPage) => riskPage?.items ?? [])
      .filter((risk) => risk.event_source === "story_v2" && (risk.evidence_article_count ?? 0) >= 2)
      .sort((left, right) => riskActivityTime(right) - riskActivityTime(left))
      .slice(0, RISK_ARTICLE_LIMIT), [riskPages]);

  const error = companiesError ? getErrorMessage(companiesError) : null;
  const todayKey = new Date().toLocaleDateString("sv-SE");
  const todaySummary = dailySummaries.find((day) => day.summary_date === todayKey);
  const todayCount = todaySummary?.eligible_story_count ?? 0;
  const todayRiskCount = todaySummary?.eligible_risk_story_count ?? 0;
  const sevenDayCount = dailySummaries.reduce((sum, day) => sum + (day.eligible_story_count ?? 0), 0);
  const sevenDayRiskCount = dailySummaries.reduce((sum, day) => sum + (day.eligible_risk_story_count ?? 0), 0);
  const todaySentiment = {
    positiveCount: todaySummary?.eligible_positive_story_count ?? 0,
    negativeCount: todaySummary?.eligible_negative_story_count ?? 0,
    neutralCount: todaySummary?.eligible_neutral_story_count ?? 0,
  };
  const sevenDaySentiment = {
    positiveCount: dailySummaries.reduce((sum, day) => sum + (day.eligible_positive_story_count ?? 0), 0),
    negativeCount: dailySummaries.reduce((sum, day) => sum + (day.eligible_negative_story_count ?? 0), 0),
    neutralCount: dailySummaries.reduce((sum, day) => sum + (day.eligible_neutral_story_count ?? 0), 0),
  };
  const periodLabel = ratioPeriod === "today" ? "1일" : "7일";
  const selectedRiskRatio = ratioPeriod === "today"
    ? { eligibleCount: todayCount, riskCount: todayRiskCount }
    : { eligibleCount: sevenDayCount, riskCount: sevenDayRiskCount };
  const selectedSentimentRatio = ratioPeriod === "today" ? todaySentiment : sevenDaySentiment;

  return <section className="workspace main-workspace briefing-workspace">
    <div className="briefing-page-head">
      <p className="main-page-intro briefing-description">실시간으로 수집한 기사를 모델이 분석하고, AI가 위험 여부와 유형을 분류·판단한 결과입니다.</p>
      <label className="briefing-company-picker"><span>분석 기업</span><select value={selectedCompanyId ? String(selectedCompanyId) : ""} onChange={(event) => selectCompany(event.target.value)} disabled={!companies.length}><option value="" disabled>기업을 선택하세요</option>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="비교 기업">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></label>
    </div>
    <div className="main-page-shell briefing-shell">
      {error && <div className="notice error">{error}</div>}
      {loading ? <p className="empty-state">브리핑을 불러오는 중입니다.</p> : !selectedCompany ? <p className="empty-state">등록된 기업 정보가 없습니다.</p> : <div className="briefing-grid">
        <div className="briefing-trends">
          <section className="panel briefing-trend-panel">
            <PanelTitle title={selectedCompany.name} description={selectedCompany.company_role === "main" ? "나의 기업" : "비교 기업"} />
            <RiskOverviewTrendChart days={dailySummaries} displayDates={trendDisplayDates} ariaLabel={`${selectedCompany.name} 최근 7일 위험 판정 기사와 부정 기사 비율`} />
          </section>
          <section className="panel briefing-trend-panel">
            <PanelTitle title="전체 평균" description={`등록 기업 ${formatNumber(companies.length)}곳 기준`} />
            <RiskOverviewTrendChart days={averageSummaries} displayDates={trendDisplayDates} ariaLabel="등록 기업 전체의 최근 7일 평균 위험 판정 기사와 부정 기사 비율" />
          </section>
        </div>
        <div className="briefing-side">
          <section className="panel briefing-company-panel">
            <PanelTitle title={selectedCompany.name} description={selectedCompany.company_role === "main" ? "나의 기업" : "비교 기업"} />
            <div className="briefing-company-ratio-section">
              <div className="briefing-ratio-head">
                <div className="briefing-ratio-tabs" role="tablist" aria-label="비율 종류">
                  <button type="button" role="tab" aria-selected={ratioView === "risk"} className={ratioView === "risk" ? "active" : ""} onClick={() => setRatioView("risk")}>위험 비율</button>
                  <button type="button" role="tab" aria-selected={ratioView === "sentiment"} className={ratioView === "sentiment" ? "active" : ""} onClick={() => setRatioView("sentiment")}>부정 비율</button>
                </div>
                <div className="briefing-period-tabs" role="tablist" aria-label="비율 기간">
                  <button type="button" role="tab" aria-selected={ratioPeriod === "today"} className={ratioPeriod === "today" ? "active" : ""} onClick={() => setRatioPeriod("today")}>1일</button>
                  <button type="button" role="tab" aria-selected={ratioPeriod === "sevenDays"} className={ratioPeriod === "sevenDays" ? "active" : ""} onClick={() => setRatioPeriod("sevenDays")}>7일</button>
                </div>
              </div>
              <div className="briefing-ratio-content" role="tabpanel" aria-label={ratioView === "risk" ? "위험 비율" : "긍정 부정 중립 비율"}>
                {ratioView === "risk"
                  ? <RiskRatioCard periodLabel={periodLabel} {...selectedRiskRatio} />
                  : <SentimentRatioCard periodLabel={periodLabel} {...selectedSentimentRatio} />}
              </div>
            </div>
          </section>
          <section className="panel briefing-risk-articles">
            <div className="briefing-risk-head"><PanelTitle title="최근 위험 사건" description="자세히 보기를 누르면 위험 판정 또는 종료 이력을 확인할 수 있습니다." /></div>
            <div className="briefing-risk-list">{riskyStories.length ? riskyStories.map((risk) => {
              const isClosed = risk.status === "closed";
              const activityDate = riskActivityDate(risk);
              const openRisk = () => isClosed
                ? onOpenResponseHistory(selectedCompanyId, risk.id)
                : onOpenCompany(selectedCompanyId, risk.id);
              return <button className="briefing-risk-card" type="button" onClick={openRisk} key={risk.id} aria-label={`${riskEventTitle(risk)} 자세히 보기`}>
                <div className="briefing-risk-meta"><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><span>{RISK_TYPE_LABELS[risk.primary_type] ?? risk.primary_type ?? "위험"}</span><span className={`briefing-risk-state ${isClosed ? "closed" : "active"}`}>{isClosed ? "종료" : "활성"}</span></div>
                <strong className="briefing-risk-story-title">{riskEventTitle(risk)}</strong>
                <p>기사 {formatNumber(risk.evidence_article_count)}건 · 출처 {formatNumber(risk.source_count)}곳</p>
                <footer><small>위험도 {formatRiskProbability(risk.risk_probability)} · {isClosed ? "종료" : "최근"} {formatDate(activityDate)}</small><span className="briefing-risk-action" aria-hidden="true">자세히 보기</span></footer>
              </button>;
            }) : <p className="panel-empty">표시할 위험 사건이 없습니다.</p>}</div>
            <div className="briefing-risk-more"><button type="button" aria-label={`${selectedCompany.name} 활성 위험 판정 더보기`} onClick={() => onOpenRiskPage(selectedCompanyId)}>더보기</button></div>
          </section>
        </div>
      </div>}
    </div>
  </section>;
}
