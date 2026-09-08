import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { Pagination, PanelTitle } from "../../shared/components";
import RiskOverviewTrendChart from "../../shared/RiskOverviewTrendChart";
import {
  RISK_TYPE_LABELS,
  formatNumber,
  formatPercent,
  formatRiskProbability,
  riskEventTitle,
} from "../../shared/presentation";
import { useSharedResource } from "../../shared/useSharedResource";
import { resolveSelectedCompany, setSelectedCompanyId as rememberSelectedCompanyId } from "../../shared/selectedCompanySession";
import AnalysisPeriodControl from "../../shared/AnalysisPeriodControl";
import { useAnalysisPeriod } from "../../shared/useAnalysisPeriod";

const RISK_PAGE_SIZE = 5;
// 기간 전체 사건을 한 번에 받아 두고 화면에서 걸러 낸다. 수준·키워드 필터가
// 매번 서버를 다시 부르면 담당자가 검색어를 고칠 때마다 목록이 껌뻑인다.
const RISK_FETCH_SIZE = 100;

const SEVERITY_BANDS = [
  { id: "critical", label: "긴급", tone: "critical" },
  { id: "warning", label: "주의", tone: "warning" },
  { id: "watch", label: "관찰", tone: "watch" },
];
const severityBandOf = (risk) => risk?.severity === "critical" ? "critical"
  : risk?.severity === "warning" ? "warning" : "watch";

const RESPONSE_STATUS_SUMMARIES = {
  pending: "대응 방안을 생성할 준비를 하고 있습니다.",
  generating: "사건 근거를 검토해 대응 방안을 생성하고 있습니다.",
  generated: "생성된 대응 방안을 확인해 주세요.",
  deferred: "대응 방안 생성이 보류되었습니다.",
  failed: "대응 방안 생성에 실패했습니다.",
  idle: "아직 생성된 대응 방안이 없습니다.",
};

const textValue = (...values) => values.find((value) => typeof value === "string" && value.trim())?.trim() ?? null;
const countValueText = (value) => `${formatNumber(value)}건`;

function mean(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function averageCompanyRisk(groups) {
  const totals = { story: 0, risk: 0, nonRisk: 0 };
  const ratios = groups.flatMap((days) => {
    const story = days.reduce((sum, day) => sum + Math.max(Number(day.eligible_story_count) || 0, 0), 0);
    const risk = days.reduce((sum, day) => sum + Math.max(Number(day.eligible_risk_story_count) || 0, 0), 0);
    const nonRisk = days.reduce((sum, day) => sum + Math.max(Number(day.eligible_non_risk_story_count) || 0, 0), 0);
    totals.story += story;
    totals.risk += risk;
    totals.nonRisk += nonRisk;
    return story > 0 ? [{ risk: risk / story, nonRisk: nonRisk / story }] : [];
  });
  return {
    ratios: {
      risk: mean(ratios.map((item) => item.risk)),
      nonRisk: mean(ratios.map((item) => item.nonRisk)),
    },
    totals,
  };
}

function averageCompanySentiment(groups) {
  const totals = { story: 0, positive: 0, negative: 0, neutral: 0, pending: 0 };
  const ratios = groups.flatMap((days) => {
    const story = days.reduce((sum, day) => sum + Math.max(Number(day.eligible_story_count) || 0, 0), 0);
    const positive = days.reduce((sum, day) => sum + Math.max(Number(day.eligible_positive_story_count) || 0, 0), 0);
    const negative = days.reduce((sum, day) => sum + Math.max(Number(day.eligible_negative_story_count) || 0, 0), 0);
    const neutral = days.reduce((sum, day) => sum + Math.max(Number(day.eligible_neutral_story_count) || 0, 0), 0);
    const pending = Math.max(story - positive - negative - neutral, 0);
    totals.story += story;
    totals.positive += positive;
    totals.negative += negative;
    totals.neutral += neutral;
    totals.pending += pending;
    return story > 0 ? [{ positive: positive / story, negative: negative / story, neutral: neutral / story, pending: pending / story }] : [];
  });
  return {
    ratios: {
      positive: mean(ratios.map((item) => item.positive)),
      negative: mean(ratios.map((item) => item.negative)),
      neutral: mean(ratios.map((item) => item.neutral)),
      pending: mean(ratios.map((item) => item.pending)),
    },
    totals,
  };
}

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
  const ratiosByDate = new Map();
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
      eligible_non_risk_story_count: 0,
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
    current.eligible_non_risk_story_count += day.eligible_non_risk_story_count ?? 0;
    current.story_count += day.story_count ?? 0;
    current.risk_event_count += day.risk_event_count ?? 0;
    byDate.set(day.summary_date, current);

    const eligibleStoryCount = Math.max(Number(day.eligible_story_count) || 0, 0);
    if (eligibleStoryCount > 0) {
      const ratios = ratiosByDate.get(day.summary_date) ?? { risk: 0, negative: 0, samples: 0 };
      ratios.risk += Math.min(Math.max(Number(day.eligible_risk_story_count) || 0, 0) / eligibleStoryCount, 1);
      ratios.negative += Math.min(Math.max(Number(day.eligible_negative_story_count) || 0, 0) / eligibleStoryCount, 1);
      ratios.samples += 1;
      ratiosByDate.set(day.summary_date, ratios);
    }
  });
  return [...byDate.values()]
    .map((day) => {
      const ratios = ratiosByDate.get(day.summary_date);
      return {
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
        eligible_non_risk_story_count: oneDecimal(day.eligible_non_risk_story_count),
        eligible_risk_story_ratio: ratios?.samples ? ratios.risk / ratios.samples : 0,
        eligible_negative_story_ratio: ratios?.samples ? ratios.negative / ratios.samples : 0,
        story_count: oneDecimal(day.story_count),
        risk_event_count: oneDecimal(day.risk_event_count),
      };
    });
}

function InteractiveDonut({ periodLabel, segments, ariaLabel, tooltipId, valueFormatter = countValueText }) {
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
          aria-label={`${slice.label} ${valueFormatter(slice.value)}`}
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
        <b>{valueFormatter(hoveredSlice.value)}</b>
      </span>
    </div>}
  </div>;
}

function RiskRatioCard({ periodLabel, storyCount, riskCount, nonRiskCount, average = null }) {
  const safeStoryCount = Math.max(Number(storyCount) || 0, 0);
  const safeRiskCount = Math.max(Number(riskCount) || 0, 0);
  const safeNonRiskCount = Math.max(Number(nonRiskCount) || 0, 0);
  const isAverage = average !== null;
  const riskRatio = isAverage ? average.ratios.risk : safeStoryCount > 0 ? safeRiskCount / safeStoryCount : 0;
  const nonRiskRatio = isAverage ? average.ratios.nonRisk : safeStoryCount > 0 ? safeNonRiskCount / safeStoryCount : 0;
  const displayCounts = isAverage ? average.totals : { story: safeStoryCount, risk: safeRiskCount, nonRisk: safeNonRiskCount };
  return <article className="briefing-ratio-card">
    <InteractiveDonut
      periodLabel={periodLabel}
      segments={[
        { key: "risk", label: "위험 이슈", value: isAverage ? riskRatio : safeRiskCount, className: "risk" },
        { key: "normal", label: "비위험 이슈", value: isAverage ? nonRiskRatio : safeNonRiskCount, className: "normal" },
      ]}
      ariaLabel={isAverage
        ? `${periodLabel} 등록 기업 평균 위험 ${formatPercent(riskRatio)}, 비위험 ${formatPercent(nonRiskRatio)}`
        : `${periodLabel} 판정 완료 이슈 ${safeStoryCount}건 중 위험 ${safeRiskCount}건, 비위험 ${safeNonRiskCount}건`}
      tooltipId="risk-ratio-tooltip"
      valueFormatter={isAverage ? formatPercent : countValueText}
    />
    <dl>
      <div className="risk"><dt><i className="risk" />위험 이슈</dt><dd>{formatPercent(riskRatio)} · {formatNumber(displayCounts.risk)}건</dd></div>
      <div className="normal"><dt><i className="normal" />비위험 이슈</dt><dd>{formatPercent(nonRiskRatio)} · {formatNumber(displayCounts.nonRisk)}건</dd></div>
    </dl>
  </article>;
}

function SentimentRatioCard({ periodLabel, storyCount, positiveCount, negativeCount, neutralCount, average = null }) {
  const stories = Math.max(Number(storyCount) || 0, 0);
  const positive = Math.max(Number(positiveCount) || 0, 0);
  const negative = Math.max(Number(negativeCount) || 0, 0);
  const neutral = Math.max(Number(neutralCount) || 0, 0);
  const pending = Math.max(stories - positive - negative - neutral, 0);
  const isAverage = average !== null;
  const positiveRatio = isAverage ? average.ratios.positive : stories > 0 ? positive / stories : 0;
  const negativeRatio = isAverage ? average.ratios.negative : stories > 0 ? negative / stories : 0;
  const neutralRatio = isAverage ? average.ratios.neutral : stories > 0 ? neutral / stories : 0;
  const pendingRatio = isAverage ? average.ratios.pending : stories > 0 ? pending / stories : 0;
  const displayCounts = isAverage ? average.totals : { story: stories, positive, negative, neutral, pending };
  return <article className="briefing-ratio-card">
    <InteractiveDonut
      periodLabel={periodLabel}
      segments={[
        { key: "positive", label: "긍정", value: isAverage ? positiveRatio : positive, className: "positive" },
        { key: "negative", label: "부정", value: isAverage ? negativeRatio : negative, className: "negative" },
        { key: "neutral", label: "중립", value: isAverage ? neutralRatio : neutral, className: "neutral" },
        { key: "pending", label: "분석 대기", value: isAverage ? pendingRatio : pending, className: "pending" },
      ]}
      ariaLabel={isAverage
        ? `${periodLabel} 판정 완료 이슈 ${displayCounts.story}건의 등록 기업 평균: 긍정 ${formatPercent(positiveRatio)}, 부정 ${formatPercent(negativeRatio)}, 중립 ${formatPercent(neutralRatio)}, 분석 대기 ${formatPercent(pendingRatio)}`
        : `${periodLabel} 판정 완료 이슈 ${stories}건 중 긍정 ${positive}건, 부정 ${negative}건, 중립 ${neutral}건, 분석 대기 ${pending}건`}
      tooltipId="sentiment-ratio-tooltip"
      valueFormatter={isAverage ? formatPercent : countValueText}
    />
    <dl>
      <div className="positive"><dt><i className="positive" />긍정 이슈</dt><dd>{formatPercent(positiveRatio)} · {formatNumber(displayCounts.positive)}건</dd></div>
      <div className="negative"><dt><i className="negative" />부정 이슈</dt><dd>{formatPercent(negativeRatio)} · {formatNumber(displayCounts.negative)}건</dd></div>
      <div className="neutral"><dt><i className="neutral" />중립 이슈</dt><dd>{formatPercent(neutralRatio)} · {formatNumber(displayCounts.neutral)}건</dd></div>
      {displayCounts.pending > 0 && <div className="pending"><dt><i className="pending" />분석 대기</dt><dd>{formatPercent(pendingRatio)} · {formatNumber(displayCounts.pending)}건</dd></div>}
    </dl>
  </article>;
}

// 로그인 직후 나의 기업과 등록 기업 평균을 비교하고 선택 기간의 위험 사건과 대응을 브리핑한다.
export default function MainPage({ onOpenCompany }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [briefingView, setBriefingView] = useState("company");
  const [ratioView, setRatioView] = useState("risk");
  const { period, changePeriod } = useAnalysisPeriod();
  const [riskPage, setRiskPage] = useState(1);
  const [riskListView, setRiskListView] = useState("risk");
  const [severityFilter, setSeverityFilter] = useState("");
  const [keyword, setKeyword] = useState("");
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
  }, [selectedCompanyId, period.start, period.end, severityFilter, keyword]);

  const selectCompany = (companyId) => {
    rememberSelectedCompanyId(companyId);
    setRiskPage(1);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("companyId", companyId);
      return next;
    });
  };

  const { data: dailyGroups = [], error: dailyError, loading: dailyLoading } = useSharedResource(
    companyIds ? `main-briefing-daily:judged:${companyIds}:${period.start}:${period.end}` : "skip:main-briefing-daily",
    companyIds
      ? () => Promise.all(companies.map((company) => api.get(`/companies/${company.id}/daily-summaries?start_date=${period.start}&end_date=${period.end}`).then((response) => response.data)))
      : () => Promise.resolve([]),
  );
  const selectedIndex = companies.findIndex((company) => company.id === selectedCompanyId);
  const dailySummaries = selectedIndex >= 0 ? dailyGroups[selectedIndex] ?? [] : [];
  const averageSummaries = averageDailySummaries(dailyGroups);
  const briefingSummaries = briefingView === "average" ? averageSummaries : dailySummaries;
  const trendDisplayDates = briefingSummaries
    .filter((day) => (day.eligible_story_count ?? 0) > 0)
    .map((day) => day.summary_date);

  const riskQueryKey = selectedCompanyId
    ? `main-briefing-risks:judged:${selectedCompanyId}:${period.start}:${period.end}`
    : "skip:main-briefing-risks";
  const { data: loadedRiskPageData, error: riskPageError } = useSharedResource(
    riskQueryKey,
    selectedCompanyId
      ? async () => {
          const params = new URLSearchParams({
            view: "all", page: "1", page_size: String(RISK_FETCH_SIZE), response: "all",
            start_date: period.start, end_date: period.end,
          });
          const response = await api.get(`/companies/${selectedCompanyId}/risk-events/page?${params}`);
          const pageData = response.data ?? { items: [], total: 0 };
          return { ...pageData, items: pageData.items ?? [], queryKey: riskQueryKey };
        }
      : () => Promise.resolve({ items: [], total: 0, queryKey: riskQueryKey }),
  );
  const riskPageData = loadedRiskPageData?.queryKey === riskQueryKey ? loadedRiskPageData : null;
  const allRiskEvents = riskPageData?.items ?? [];

  // 담당자가 실제로 하는 일은 "이 기간에 어떤 위험이 있었고 뭘 해야 하나" 찾기다.
  // 수준·키워드로 좁힌 결과만 목록에 남긴다.
  const filteredRiskEvents = allRiskEvents.filter((risk) => {
    if (severityFilter && severityBandOf(risk) !== severityFilter) return false;
    if (!keyword.trim()) return true;
    const needle = keyword.trim().toLowerCase();
    const haystack = [
      riskEventTitle(risk),
      ...(risk.risk_types ?? []).map((item) => RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type),
    ].join(" ").toLowerCase();
    return haystack.includes(needle);
  });
  const riskTotal = filteredRiskEvents.length;
  const pageStart = (riskPage - 1) * RISK_PAGE_SIZE;
  const visibleRiskEvents = filteredRiskEvents.slice(pageStart, pageStart + RISK_PAGE_SIZE);

  // 대응 방안 요약은 화면에 실제로 보이는 사건만 받아 온다.
  const visibleIds = visibleRiskEvents.map((risk) => risk.id).join(",");
  const { data: draftSummaries = {} } = useSharedResource(
    visibleIds ? `main-briefing-drafts:${visibleIds}` : "skip:main-briefing-drafts",
    visibleIds
      ? async () => {
          const entries = await Promise.all(visibleRiskEvents.map(async (risk) => {
            try {
              const draftResponse = await api.get(`/risk-events/${risk.id}/response-drafts`);
              const drafts = draftResponse.data ?? [];
              const latestDraft = drafts.find((draft) => draft.schema_version === 3) ?? drafts[0] ?? null;
              return [risk.id, responseDraftSummary(risk, latestDraft)];
            } catch {
              return [risk.id, RESPONSE_STATUS_SUMMARIES[risk.response_generation_status] ?? RESPONSE_STATUS_SUMMARIES.idle];
            }
          }));
          return Object.fromEntries(entries);
        }
      : () => Promise.resolve({}),
  );

  const severityCounts = SEVERITY_BANDS.map((band) => ({
    ...band,
    count: allRiskEvents.filter((risk) => severityBandOf(risk) === band.id).length,
  }));

  useEffect(() => {
    const lastPage = Math.max(1, Math.ceil(riskTotal / RISK_PAGE_SIZE));
    if (riskPage > lastPage) setRiskPage(lastPage);
  }, [riskPage, riskTotal]);

  const error = companiesError || riskPageError || dailyError
    ? getErrorMessage(companiesError ?? riskPageError ?? dailyError) : null;
  const periodLabel = `${period.start} ~ ${period.end}`;
  const periodStoryCount = briefingSummaries.reduce((sum, day) => sum + (day.eligible_story_count ?? 0), 0);
  const periodRiskCount = briefingSummaries.reduce((sum, day) => sum + (day.eligible_risk_story_count ?? 0), 0);
  const selectedRiskRatio = briefingView === "average"
    ? { average: averageCompanyRisk(dailyGroups) }
    : {
        storyCount: periodStoryCount,
        riskCount: periodRiskCount,
        nonRiskCount: briefingSummaries.reduce((sum, day) => sum + (day.eligible_non_risk_story_count ?? 0), 0),
      };
  const selectedSentimentRatio = briefingView === "average"
    ? { average: averageCompanySentiment(dailyGroups) }
    : {
        storyCount: periodStoryCount,
        positiveCount: briefingSummaries.reduce((sum, day) => sum + (day.eligible_positive_story_count ?? 0), 0),
        negativeCount: briefingSummaries.reduce((sum, day) => sum + (day.eligible_negative_story_count ?? 0), 0),
        neutralCount: briefingSummaries.reduce((sum, day) => sum + (day.eligible_neutral_story_count ?? 0), 0),
      };

  const riskRatio = periodStoryCount > 0 ? periodRiskCount / periodStoryCount : 0;
  const periodNegativeCount = briefingSummaries.reduce((sum, day) => sum + (day.eligible_negative_story_count ?? 0), 0);
  const negativeRatio = periodStoryCount > 0 ? periodNegativeCount / periodStoryCount : 0;
  const respondedCount = allRiskEvents.filter((risk) => risk.response_generation_status === "generated").length;

  return <section className="workspace main-workspace briefing-workspace">
    {/* 담당자가 화면을 열자마자 알아야 하는 것: 어느 기업을, 언제 기준으로 보고 있는가 */}
    <section className="panel briefing-hero">
      <div className="briefing-hero-copy">
        <p className="eyebrow">AI RISK BRIEFING</p>
        <h1>{selectedCompany ? selectedCompany.name + "에 지금 어떤 위험이 있나요?" : "AI 리스크 브리핑"}</h1>
        <p className="briefing-hero-description">실시간으로 수집한 기사를 모델이 분석하고, AI가 위험 여부와 유형을 분류·판단한 결과입니다.</p>
      </div>
      <div className="briefing-hero-controls">
        <label className="briefing-company-picker"><span>분석 기업</span><select value={selectedCompanyId ? String(selectedCompanyId) : ""} onChange={(event) => selectCompany(event.target.value)} disabled={!companies.length}><option value="" disabled>기업을 선택하세요</option>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="비교 기업">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></label>
        <AnalysisPeriodControl period={period} onChange={(field, value) => { setRiskPage(1); changePeriod(field, value); }} />
      </div>
    </section>

    <div className="main-page-shell briefing-shell">
      {error && <div className="notice error">{error}</div>}
      {loading ? <p className="empty-state">브리핑을 불러오는 중입니다.</p> : !selectedCompany ? <p className="empty-state">등록된 기업 정보가 없습니다.</p> : <div className="briefing-grid">

        <section className="briefing-kpi-grid" aria-label="선택 기간 요약">
          <article className="panel briefing-kpi danger">
            <p className="briefing-kpi-label">위험 판정률</p>
            <strong>{formatPercent(riskRatio)}</strong>
            <small>판정 대상 {formatNumber(periodStoryCount)}건 중 {formatNumber(periodRiskCount)}건</small>
          </article>
          <article className="panel briefing-kpi warning">
            <p className="briefing-kpi-label">위험 이슈</p>
            <strong>{formatNumber(allRiskEvents.length)}건</strong>
            <small>대응 방안 생성 완료 {formatNumber(respondedCount)}건</small>
          </article>
          <article className="panel briefing-kpi">
            <p className="briefing-kpi-label">부정 이슈 비율</p>
            <strong>{formatPercent(negativeRatio)}</strong>
            <small>부정 {formatNumber(periodNegativeCount)}건</small>
          </article>
          <article className="panel briefing-kpi success">
            <p className="briefing-kpi-label">판정 대상 신호</p>
            <strong>{formatNumber(periodStoryCount)}건</strong>
            <small>{periodLabel}</small>
          </article>
        </section>

        <section className="briefing-insight-grid">
          <article className="panel briefing-severity-panel">
            <div className="briefing-overview-head">
              <PanelTitle kicker="RISK LEVEL" title="위험 수준 분포" description={selectedCompany.name + " · " + periodLabel} />
            </div>
            {allRiskEvents.length ? <div className="briefing-severity-bars">
              {severityCounts.map((band) => {
                const ratio = allRiskEvents.length ? band.count / allRiskEvents.length : 0;
                return <div className="briefing-severity-row" key={band.id}>
                  <div className="briefing-severity-head">
                    <span>{band.label}</span>
                    <strong>{formatNumber(band.count)}건 · {formatPercent(ratio)}</strong>
                  </div>
                  <div className="briefing-severity-track">
                    <i className={band.tone} style={{ width: Math.round(ratio * 100) + "%" }} />
                  </div>
                </div>;
              })}
            </div> : <p className="panel-empty">선택한 기간에 위험 사건이 없습니다.</p>}
          </article>

          <article className="panel briefing-overview-panel">
            <div className="briefing-overview-head">
              <PanelTitle
                kicker="COMPOSITION"
                title={briefingView === "average" ? "전체 평균" : selectedCompany.name}
                description={briefingView === "average" ? "등록 기업 " + formatNumber(companies.length) + "곳 기준" : selectedCompany.company_role === "main" ? "나의 기업" : "비교 기업"}
              />
              <div className="briefing-overview-controls">
                <div className="briefing-view-tabs" role="tablist" aria-label="브리핑 비교 기준">
                  <button id="briefing-company-tab" type="button" role="tab" aria-selected={briefingView === "company"} aria-controls="briefing-overview-charts" className={briefingView === "company" ? "active" : ""} onClick={() => setBriefingView("company")}>{selectedCompany.company_role === "main" ? "나의 기업" : "비교 기업"}</button>
                  <button id="briefing-average-tab" type="button" role="tab" aria-selected={briefingView === "average"} aria-controls="briefing-overview-charts" className={briefingView === "average" ? "active" : ""} onClick={() => setBriefingView("average")}>전체 평균</button>
                </div>
              </div>
            </div>
            {dailyLoading ? <p className="panel-empty" role="status">선택한 기간의 데이터를 불러오는 중입니다.</p> : dailyError ? <p className="panel-empty">선택한 기간의 데이터를 불러오지 못했습니다.</p> : <div id="briefing-overview-charts" role="tabpanel" aria-labelledby={"briefing-" + briefingView + "-tab"}>
              <div className="briefing-ratio-tabs" role="tablist" aria-label="비율 종류">
                <button type="button" role="tab" aria-selected={ratioView === "risk"} className={ratioView === "risk" ? "active" : ""} onClick={() => setRatioView("risk")}>위험 비율</button>
                <button type="button" role="tab" aria-selected={ratioView === "sentiment"} className={ratioView === "sentiment" ? "active" : ""} onClick={() => setRatioView("sentiment")}>부정 비율</button>
              </div>
              <div className="briefing-ratio-content" role="tabpanel" aria-label={ratioView === "risk" ? "위험 비율" : "긍정 부정 중립 비율"}>
                {ratioView === "risk"
                  ? <RiskRatioCard periodLabel={periodLabel} {...selectedRiskRatio} />
                  : <SentimentRatioCard periodLabel={periodLabel} {...selectedSentimentRatio} />}
              </div>
            </div>}
          </article>
        </section>

        <section className="panel briefing-trend-panel" aria-label={periodLabel + " 위험 및 부정 비율 추이"}>
          <div className="briefing-overview-head">
            <PanelTitle kicker="TREND" title="날짜별 위험·부정 통계" description={periodLabel} />
          </div>
          <div className="briefing-trend-pane">
            <RiskOverviewTrendChart
              legendTitle=""
              days={briefingSummaries}
              displayDates={trendDisplayDates}
              basis="stories"
              ariaLabel={briefingView === "average" ? "등록 기업 전체의 " + periodLabel + " 평균 위험 이슈와 부정 이슈 비율" : selectedCompany.name + " " + periodLabel + " 위험 이슈와 부정 이슈 비율"}
            />
          </div>
        </section>

        <section className="panel briefing-risk-articles">
          <div className="briefing-overview-head">
            <PanelTitle kicker="RISK EVENTS" title="위험 사건 찾기" description="수준과 키워드로 좁혀 보고, 카드를 누르면 상세 분석으로 이동합니다." />
            <span className="pipeline-risk-count-pill">{formatNumber(riskTotal)}건</span>
          </div>
          <div className="briefing-event-filters">
            <label><span>위험 수준</span>
              <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}>
                <option value="">전체 수준</option>
                {SEVERITY_BANDS.map((band) => <option key={band.id} value={band.id}>{band.label}</option>)}
              </select>
            </label>
            <label className="briefing-event-search"><span>키워드</span>
              <input type="search" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="사건 제목·위험 유형 검색" />
            </label>
            <div className="story-view-tabs" role="tablist" aria-label="목록 표시 내용">
              <button id="briefing-risk-tab" type="button" role="tab" aria-selected={riskListView === "risk"} aria-controls="briefing-risk-content" className={riskListView === "risk" ? "active" : ""} onClick={() => setRiskListView("risk")}>위험 사건</button>
              <button id="briefing-response-tab" type="button" role="tab" aria-selected={riskListView === "response"} aria-controls="briefing-risk-content" className={riskListView === "response" ? "active" : ""} onClick={() => setRiskListView("response")}>대응 방안</button>
            </div>
          </div>
          <div id="briefing-risk-content" className="briefing-event-list" role="tabpanel" aria-labelledby={"briefing-" + riskListView + "-tab"}>
            {!riskPageData && !riskPageError ? <p className="panel-empty">선택한 기간의 위험 사건을 불러오는 중입니다.</p>
              : riskPageError && !riskPageData ? <p className="panel-empty">선택한 기간의 위험 사건을 불러오지 못했습니다.</p>
              : visibleRiskEvents.length ? visibleRiskEvents.map((risk) => {
                const band = SEVERITY_BANDS.find((item) => item.id === severityBandOf(risk));
                const types = [...(risk.risk_types ?? [])].sort((left, right) => Number(right.is_primary) - Number(left.is_primary));
                return <button className="briefing-event-card" type="button" onClick={() => onOpenCompany(selectedCompanyId, risk.id, { stage: riskListView === "response" ? "response" : "risk" })} key={risk.id} aria-label={riskEventTitle(risk) + (riskListView === "response" ? " 대응 방안 보기" : " 위험 판정 근거 보기")}>
                  <div className="briefing-event-meta">
                    <span className={"briefing-event-level " + band.tone}>{band.label}</span>
                    {types.slice(0, 2).map((item) => <span className="briefing-event-type" key={item.risk_type}>{RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type}</span>)}
                    <span className="briefing-event-score">위험도 {formatRiskProbability(risk.risk_probability)}</span>
                  </div>
                  <strong className="briefing-event-title">{riskEventTitle(risk)}</strong>
                  {riskListView === "response"
                    ? <span className="briefing-event-response">{draftSummaries[risk.id] ?? RESPONSE_STATUS_SUMMARIES[risk.response_generation_status] ?? RESPONSE_STATUS_SUMMARIES.idle}</span>
                    : <span className="briefing-event-response muted">관련 보도 {formatNumber(risk.evidence_article_count ?? 0)}건 · 출처 {formatNumber(risk.source_count ?? 0)}곳</span>}
                </button>;
              }) : <p className="panel-empty">조건에 맞는 위험 사건이 없습니다.</p>}
          </div>
          <Pagination page={riskPage} pageSize={RISK_PAGE_SIZE} total={riskTotal} onChange={setRiskPage} />
        </section>
      </div>}
    </div>
  </section>;
}
