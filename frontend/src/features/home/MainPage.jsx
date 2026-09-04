import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { Pagination, PanelTitle } from "../../shared/components";
import RiskOverviewTrendChart from "../../shared/RiskOverviewTrendChart";
import {
  formatNumber,
  formatPercent,
  riskEventTitle,
} from "../../shared/presentation";
import { useSharedResource } from "../../shared/useSharedResource";
import { resolveSelectedCompany, setSelectedCompanyId as rememberSelectedCompanyId } from "../../shared/selectedCompanySession";

const MAIN_TREND_DAYS = 7;
const RISK_PAGE_SIZE = 3;

const RESPONSE_STATUS_SUMMARIES = {
  pending: "대응 방안을 생성할 준비를 하고 있습니다.",
  generating: "사건 근거를 검토해 대응 방안을 생성하고 있습니다.",
  generated: "생성된 대응 방안을 확인해 주세요.",
  deferred: "대응 방안 생성이 보류되었습니다.",
  failed: "대응 방안 생성에 실패했습니다.",
  idle: "아직 생성된 대응 방안이 없습니다.",
};

const textValue = (...values) => values.find((value) => typeof value === "string" && value.trim())?.trim() ?? null;

function firstGroupedAction(groups) {
  if (!groups || typeof groups !== "object") return null;
  const preferredKeys = ["immediate", "within_24h", "within_7d"];
  const keys = [...preferredKeys, ...Object.keys(groups).filter((key) => !preferredKeys.includes(key))];
  for (const key of keys) {
    const item = Array.isArray(groups[key]) ? groups[key][0] : null;
    const action = typeof item === "string" ? item : textValue(item?.action, item?.task);
    if (action) return action;
  }
  return null;
}

function responseDraftSummary(risk, draft) {
  const content = draft?.content ?? {};
  if (content.status === "근거부족_보류") {
    return textValue(content.review_reason, RESPONSE_STATUS_SUMMARIES.deferred);
  }

  if (draft?.schema_version === 3 && draft.generation_kind === "competitor_impact") {
    return textValue(
      content.recommendation?.headline,
      content.recommendation?.recommendations?.[0]?.action,
      content.impact?.reason,
      content.status === "영향없음_종료" ? "나의 기업에 미치는 영향 경로가 확인되지 않았습니다." : null,
    ) ?? RESPONSE_STATUS_SUMMARIES[risk.response_generation_status] ?? RESPONSE_STATUS_SUMMARIES.idle;
  }

  if (draft?.schema_version === 3) {
    const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
    const scenario = scenarios.find((item) => item.stance === content.selected_stance) ?? scenarios[0];
    const report = scenario?.report ?? {};
    const strategy = Array.isArray(report.strategies) ? report.strategies[0] : null;
    const checklist = Array.isArray(report.checklist) ? report.checklist[0] : null;
    return textValue(
      strategy?.detail,
      strategy?.title,
      checklist?.task,
      report.summary_points?.[1],
      report.summary_points?.[0],
    ) ?? RESPONSE_STATUS_SUMMARIES[risk.response_generation_status] ?? RESPONSE_STATUS_SUMMARIES.idle;
  }

  const scenario = Array.isArray(content.scenarios) ? content.scenarios[0] : null;
  return textValue(
    firstGroupedAction(scenario?.recommended_actions),
    firstGroupedAction(content.recommended_actions),
    content.recommendation?.headline,
    content.recommendation?.recommendations?.[0]?.action,
  ) ?? RESPONSE_STATUS_SUMMARIES[risk.response_generation_status] ?? RESPONSE_STATUS_SUMMARIES.idle;
}

function averageDailySummaries(groups) {
  if (!groups.length) return [];
  const oneDecimal = (value) => Math.round((value / groups.length) * 10) / 10;
  const byDate = new Map();
  groups.flat().forEach((day) => {
    const current = byDate.get(day.summary_date) ?? {
      summary_date: day.summary_date,
      article_count: 0,
      risk_article_count: 0,
      positive_article_count: 0,
      neutral_article_count: 0,
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
    current.positive_article_count += day.positive_article_count ?? 0;
    current.neutral_article_count += day.neutral_article_count ?? 0;
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
      positive_article_count: oneDecimal(day.positive_article_count),
      neutral_article_count: oneDecimal(day.neutral_article_count),
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

function InteractiveDonut({ periodLabel, segments, ariaLabel, tooltipId }) {
  const [hoveredKey, setHoveredKey] = useState(null);
  const total = segments.reduce((sum, segment) => sum + Math.max(Number(segment.value) || 0, 0), 0);
  let cumulativePercent = 0;
  const slices = segments.map((segment) => {
    const value = Math.max(Number(segment.value) || 0, 0);
    const percent = total > 0 ? value / total * 100 : 0;
    const slice = { ...segment, value, percent, offset: -cumulativePercent };
    cumulativePercent += percent;
    return slice;
  });
  const hoveredSlice = slices.find((slice) => slice.key === hoveredKey) ?? null;

  return <div className="briefing-pie-wrap">
    <div className="collection-pie">
      <svg viewBox="0 0 100 100" role="group" aria-label={ariaLabel} onPointerLeave={() => setHoveredKey(null)}>
        <circle className="collection-pie-track" cx="50" cy="50" r="40" pathLength="100" />
        {slices.filter((slice) => slice.value > 0).map((slice) => <circle
          className={`collection-pie-segment ${slice.className}${hoveredKey === slice.key ? " active" : ""}`}
          cx="50"
          cy="50"
          r="40"
          pathLength="100"
          strokeDasharray={`${slice.percent} ${100 - slice.percent}`}
          strokeDashoffset={slice.offset}
          transform="rotate(-90 50 50)"
          tabIndex="0"
          role="img"
          aria-label={`${slice.label} ${formatNumber(slice.value)}건`}
          aria-describedby={hoveredKey === slice.key ? tooltipId : undefined}
          onPointerEnter={() => setHoveredKey(slice.key)}
          onPointerLeave={() => setHoveredKey(null)}
          onFocus={() => setHoveredKey(slice.key)}
          onBlur={() => setHoveredKey(null)}
          key={slice.key}
        />)}
      </svg>
      <div aria-hidden="true" />
    </div>
    {hoveredSlice && <div className="briefing-pie-tooltip visible" id={tooltipId} role="tooltip">
      <strong>{periodLabel}</strong>
      <span>
        <span className="briefing-pie-tooltip-label">
          <i className={hoveredSlice.className} aria-hidden="true" />
          {hoveredSlice.label}
        </span>
        <b>{formatNumber(hoveredSlice.value)}건</b>
      </span>
    </div>}
  </div>;
}

function RiskRatioCard({ periodLabel, articleCount, riskCount }) {
  const safeArticleCount = Math.max(Number(articleCount) || 0, 0);
  const safeRiskCount = Math.min(Math.max(Number(riskCount) || 0, 0), safeArticleCount);
  const nonRiskCount = Math.max(safeArticleCount - safeRiskCount, 0);
  const riskRatio = safeArticleCount > 0 ? safeRiskCount / safeArticleCount : 0;
  const nonRiskRatio = safeArticleCount > 0 ? nonRiskCount / safeArticleCount : 0;
  return <article className="briefing-ratio-card">
    <InteractiveDonut
      periodLabel={periodLabel}
      segments={[
        { key: "risk", label: "위험", value: safeRiskCount, className: "risk" },
        { key: "normal", label: "비위험", value: nonRiskCount, className: "normal" },
      ]}
      ariaLabel={`${periodLabel} 기사 ${safeArticleCount}건 중 위험 ${safeRiskCount}건, 비위험 ${nonRiskCount}건`}
      tooltipId="risk-ratio-tooltip"
    />
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
  return <article className="briefing-ratio-card">
    <InteractiveDonut
      periodLabel={periodLabel}
      segments={[
        { key: "positive", label: "긍정", value: positive, className: "positive" },
        { key: "negative", label: "부정", value: negative, className: "negative" },
        { key: "neutral", label: "중립", value: neutral, className: "neutral" },
      ]}
      ariaLabel={`${periodLabel} 감성 판정 기사 ${total}건 중 긍정 ${positive}건, 부정 ${negative}건, 중립 ${neutral}건`}
      tooltipId="sentiment-ratio-tooltip"
    />
    <dl>
      <div className="positive"><dt><i className="positive" />긍정</dt><dd>{formatPercent(positiveRatio)} · {formatNumber(positive)}건</dd></div>
      <div className="negative"><dt><i className="negative" />부정</dt><dd>{formatPercent(negativeRatio)} · {formatNumber(negative)}건</dd></div>
      <div className="neutral"><dt><i className="neutral" />중립</dt><dd>{formatPercent(neutralRatio)} · {formatNumber(neutral)}건</dd></div>
    </dl>
  </article>;
}

// 로그인 직후 나의 기업과 등록 기업 평균을 비교하고 활성 위험 사건과 대응을 브리핑한다.
export default function MainPage({ onOpenCompany }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [briefingView, setBriefingView] = useState("company");
  const [ratioView, setRatioView] = useState("risk");
  const [ratioPeriod, setRatioPeriod] = useState("sevenDays");
  const [riskPage, setRiskPage] = useState(1);
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

  useEffect(() => {
    setRiskPage(1);
  }, [selectedCompanyId]);

  const selectCompany = (companyId) => {
    rememberSelectedCompanyId(companyId);
    setRiskPage(1);
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
  const briefingSummaries = briefingView === "average" ? averageSummaries : dailySummaries;
  const trendDisplayDates = briefingSummaries
    .filter((day) => (day.article_count ?? 0) > 0)
    .map((day) => day.summary_date);

  const { data: riskPageData, error: riskPageError, loading: riskPageLoading } = useSharedResource(
    selectedCompanyId ? `main-briefing-risks:${selectedCompanyId}:${riskPage}` : "skip:main-briefing-risks",
    selectedCompanyId
      ? async () => {
          const response = await api.get(`/companies/${selectedCompanyId}/risk-events/page?view=active&page=${riskPage}&page_size=${RISK_PAGE_SIZE}&response=all`);
          const pageData = response.data ?? { items: [], total: 0, page: riskPage, page_size: RISK_PAGE_SIZE };
          const items = await Promise.all((pageData.items ?? []).map(async (risk) => {
            try {
              const draftResponse = await api.get(`/risk-events/${risk.id}/response-drafts`);
              const drafts = draftResponse.data ?? [];
              const latestDraft = drafts.find((draft) => draft.schema_version === 3) ?? drafts[0] ?? null;
              return { ...risk, response_summary: responseDraftSummary(risk, latestDraft) };
            } catch {
              return { ...risk, response_summary: RESPONSE_STATUS_SUMMARIES[risk.response_generation_status] ?? RESPONSE_STATUS_SUMMARIES.idle };
            }
          }));
          return { ...pageData, items };
        }
      : () => Promise.resolve({ items: [], total: 0, page: 1, page_size: RISK_PAGE_SIZE }),
  );
  const riskyStories = riskPageData?.items ?? [];
  const riskTotal = riskPageData?.total ?? 0;

  useEffect(() => {
    if (riskPageLoading) return;
    const lastPage = Math.max(1, Math.ceil(riskTotal / RISK_PAGE_SIZE));
    if (riskPage > lastPage) setRiskPage(lastPage);
  }, [riskPage, riskPageLoading, riskTotal]);

  const error = companiesError || riskPageError ? getErrorMessage(companiesError ?? riskPageError) : null;
  const todayKey = new Date().toLocaleDateString("sv-SE");
  const todaySummary = briefingSummaries.find((day) => day.summary_date === todayKey);
  const todayCount = todaySummary?.article_count ?? 0;
  const todayRiskCount = todaySummary?.risk_article_count ?? 0;
  const sevenDayCount = briefingSummaries.reduce((sum, day) => sum + (day.article_count ?? 0), 0);
  const sevenDayRiskCount = briefingSummaries.reduce((sum, day) => sum + (day.risk_article_count ?? 0), 0);
  const todaySentiment = {
    positiveCount: todaySummary?.positive_article_count ?? 0,
    negativeCount: todaySummary?.negative_article_count ?? 0,
    neutralCount: todaySummary?.neutral_article_count ?? 0,
  };
  const sevenDaySentiment = {
    positiveCount: briefingSummaries.reduce((sum, day) => sum + (day.positive_article_count ?? 0), 0),
    negativeCount: briefingSummaries.reduce((sum, day) => sum + (day.negative_article_count ?? 0), 0),
    neutralCount: briefingSummaries.reduce((sum, day) => sum + (day.neutral_article_count ?? 0), 0),
  };
  const periodLabel = ratioPeriod === "today" ? "1일" : "7일";
  const selectedRiskRatio = ratioPeriod === "today"
    ? { articleCount: todayCount, riskCount: todayRiskCount }
    : { articleCount: sevenDayCount, riskCount: sevenDayRiskCount };
  const selectedSentimentRatio = ratioPeriod === "today" ? todaySentiment : sevenDaySentiment;

  return <section className="workspace main-workspace briefing-workspace">
    <div className="briefing-page-head">
      <p className="main-page-intro briefing-description">실시간으로 수집한 기사를 모델이 분석하고, AI가 위험 여부와 유형을 분류·판단한 결과입니다.</p>
      <label className="briefing-company-picker"><span>분석 기업</span><select value={selectedCompanyId ? String(selectedCompanyId) : ""} onChange={(event) => selectCompany(event.target.value)} disabled={!companies.length}><option value="" disabled>기업을 선택하세요</option>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="비교 기업">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></label>
    </div>
    <div className="main-page-shell briefing-shell">
      {error && <div className="notice error">{error}</div>}
      {loading ? <p className="empty-state">브리핑을 불러오는 중입니다.</p> : !selectedCompany ? <p className="empty-state">등록된 기업 정보가 없습니다.</p> : <div className="briefing-grid">
        <section className="panel briefing-overview-panel">
          <div className="briefing-overview-head">
            <PanelTitle
              title={briefingView === "average" ? "전체 평균" : selectedCompany.name}
              description={briefingView === "average" ? `등록 기업 ${formatNumber(companies.length)}곳 기준` : selectedCompany.company_role === "main" ? "나의 기업" : "비교 기업"}
            />
            <div className="briefing-view-tabs" role="tablist" aria-label="브리핑 비교 기준">
              <button id="briefing-company-tab" type="button" role="tab" aria-selected={briefingView === "company"} aria-controls="briefing-overview-charts" className={briefingView === "company" ? "active" : ""} onClick={() => setBriefingView("company")}>{selectedCompany.company_role === "main" ? "나의 기업" : "비교 기업"}</button>
              <button id="briefing-average-tab" type="button" role="tab" aria-selected={briefingView === "average"} aria-controls="briefing-overview-charts" className={briefingView === "average" ? "active" : ""} onClick={() => setBriefingView("average")}>전체 평균</button>
            </div>
          </div>
          <div id="briefing-overview-charts" className="briefing-overview-charts" role="tabpanel" aria-labelledby={`briefing-${briefingView}-tab`}>
            <section className="briefing-ratio-pane" aria-label="위험 및 감성 비율">
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
            </section>
            <section className="briefing-trend-pane" aria-label="최근 7일 위험 및 부정 비율 추이">
              <div className="briefing-chart-heading"><strong>최근 7일 추이</strong><small>날짜별 위험·부정 비율</small></div>
              <RiskOverviewTrendChart
                days={briefingSummaries}
                displayDates={trendDisplayDates}
                basis="articles"
                ariaLabel={briefingView === "average" ? "등록 기업 전체의 최근 7일 평균 위험 판정 기사와 부정 기사 비율" : `${selectedCompany.name} 최근 7일 위험 판정 기사와 부정 기사 비율`}
              />
            </section>
          </div>
        </section>
        <section className="panel briefing-risk-articles">
          <div className="briefing-risk-head"><PanelTitle title="최근 위험 사건" description="현재 활성 상태인 사건과 최신 대응 방안을 확인할 수 있습니다." /></div>
          <div className="briefing-risk-columns"><strong>위험사건</strong><strong>대응 방안</strong></div>
          <div className="briefing-risk-list">{riskPageLoading && !riskPageData ? <p className="panel-empty">활성 위험 사건을 불러오는 중입니다.</p> : riskyStories.length ? riskyStories.map((risk) => <button className="briefing-risk-card" type="button" onClick={() => onOpenCompany(selectedCompanyId, risk.id)} key={risk.id} aria-label={`${riskEventTitle(risk)} 자세히 보기`}>
            <strong className="briefing-risk-story-title">{riskEventTitle(risk)}</strong>
            <span className="briefing-response-summary" title={risk.response_summary}>{risk.response_summary}</span>
          </button>) : <p className="panel-empty">현재 활성 위험 사건이 없습니다.</p>}</div>
          <Pagination page={riskPage} pageSize={RISK_PAGE_SIZE} total={riskTotal} onChange={setRiskPage} />
        </section>
      </div>}
    </div>
  </section>;
}
