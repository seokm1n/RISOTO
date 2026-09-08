import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { Pagination } from "../../shared/components";
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

import './MainPage.css';

const RISK_PAGE_SIZE = 5;

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

// summary_points는 "핵심 이슈: ..." 형태로 온다. 브리핑 한 줄에는 라벨이 군더더기다.
function stripLabel(point) {
  if (typeof point !== "string") return point;
  const matched = /^\s*[^:：]{2,14}\s*[:：]\s*([\s\S]+)$/.exec(point);
  return matched ? matched[1].trim() : point;
}

// 브리핑에서 가로로 늘어놓을 대응 요점. 전략 제목이 가장 짧고 행동으로 읽힌다.
function responseDraftPoints(draft) {
  const content = draft?.content ?? {};
  if (draft?.schema_version !== 3) return [];
  if (draft.generation_kind === "competitor_impact") {
    return (content.recommendation?.recommendations ?? [])
      .map((item) => item.action)
      .filter(Boolean)
      .slice(0, 4);
  }
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const scenario = scenarios.find((item) => item.stance === content.selected_stance) ?? scenarios[0];
  const report = scenario?.report ?? {};
  const titles = (report.strategies ?? []).map((item) => item.title).filter(Boolean);
  if (titles.length) return titles.slice(0, 4);
  return (report.checklist ?? []).map((item) => item.task).filter(Boolean).slice(0, 3);
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
    // 펼쳐서 읽는 자리라 1~2문장이 적당하다. scenario_recommendation이 그 길이로
    // 쓰이는 필드이고(현재 권고 카드와 같은 문장), 없으면 전략 상세로 내려간다.
    return textValue(
      report.scenario_recommendation,
      strategy?.detail,
      stripLabel(report.summary_points?.[0]),
      strategy?.title,
      checklist?.task,
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

function InteractiveDonut({ periodLabel, segments, ariaLabel, tooltipId, emphasisKey, valueFormatter = countValueText }) {
  const [hoveredKey, setHoveredKey] = useState(null);
  const total = segments.reduce((sum, segment) => sum + Math.max(Number(segment.value) || 0, 0), 0);
  let cumulativePercent = 0;
  const slices = segments.map((segment) => {
    const value = Math.max(Number(segment.value) || 0, 0);
    const percent = total > 0 ? value / total * 100 : 0;
    const slice = { ...segment, value, percent, offset: -cumulativePercent, emphasized: segment.key === emphasisKey };
    cumulativePercent += percent;
    return slice;
  });
  const hoveredSlice = slices.find((slice) => slice.key === hoveredKey) ?? null;

  return <div className="briefing-pie-wrap">
    <div className="collection-pie">
      <svg viewBox="0 0 100 100" role="group" aria-label={ariaLabel} onPointerLeave={() => setHoveredKey(null)}>
        <circle className="collection-pie-track" cx="50" cy="50" r="40" pathLength="100" />
        {slices.filter((slice) => slice.value > 0).sort((left, right) => Number(left.emphasized) - Number(right.emphasized)).map((slice) => <circle
          className={`collection-pie-segment ${slice.className}${slice.emphasized ? " emphasized" : ""}${hoveredKey === slice.key ? " active" : ""}`}
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
      emphasisKey="risk"
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
      emphasisKey="negative"
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
  const [expandedRiskId, setExpandedRiskId] = useState(null);
  const { data: companies = [], error: companiesError, loading } = useSharedResource(
    "/companies", () => api.get("/companies").then((response) => response.data),
  );
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

  useEffect(() => {
    setExpandedRiskId(null);
  }, [selectedCompanyId, period.start, period.end, riskPage]);

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
              return {
                ...risk,
                response_summary: responseDraftSummary(risk, latestDraft),
                response_points: responseDraftPoints(latestDraft),
              };
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

  const selectedStoryCount = dailySummaries.reduce((sum, day) => sum + (day.eligible_story_count ?? 0), 0);
  const selectedRiskCount = dailySummaries.reduce((sum, day) => sum + (day.eligible_risk_story_count ?? 0), 0);
  const selectedNegativeCount = dailySummaries.reduce((sum, day) => sum + (day.eligible_negative_story_count ?? 0), 0);
  const statisticsReady = !loading && !dailyLoading && !dailyError && Boolean(selectedCompany);
  const eventsReady = Boolean(riskPageData) && !riskPageLoading && !riskPageError;
  const statValue = (value) => statisticsReady ? value : "—";

  return <section className="workspace main-workspace briefing-workspace">
    <header className="briefing-page-head">
      <div className="briefing-company-picker">
        <label htmlFor="briefing-company">분석 기업</label>
        <h1><select id="briefing-company" value={selectedCompanyId ? String(selectedCompanyId) : ""} onChange={(event) => selectCompany(event.target.value)} disabled={loading || !companies.length}><option value="" disabled>{loading ? "불러오는 중..." : "기업을 선택하세요"}</option>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="비교 기업">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></h1>
      </div>
      <div className="briefing-page-filters">
        <AnalysisPeriodControl period={period} onChange={(field, value) => { setRiskPage(1); changePeriod(field, value); }} />
      </div>
    </header>
    {error && <div className="notice error" role="alert">{error}</div>}
    {loading ? <div className="briefing-state" role="status"><span className="briefing-state-mark" aria-hidden="true">···</span><h2>브리핑을 준비하고 있습니다</h2><p>기업의 분석 결과를 불러오는 중입니다.</p></div> : !selectedCompany ? <div className="briefing-state"><span className="briefing-state-mark" aria-hidden="true">+</span><h2>{companiesError ? "기업 정보를 불러오지 못했습니다" : "모니터링할 기업을 등록해 주세요"}</h2><p>{companiesError ? "잠시 후 화면을 새로고침해 다시 확인해 주세요." : "기업을 등록하면 수집한 뉴스와 위험 분석 결과를 이곳에서 확인할 수 있습니다."}</p>{!companiesError && <Link to="/companies" className="briefing-primary-button">기업 등록하기 <span aria-hidden="true">→</span></Link>}</div> : <>
      <section className="briefing-metrics" aria-label="선택 기업의 기간별 핵심 현황" aria-busy={dailyLoading || riskPageLoading}>
        <article className="briefing-metric"><div className="briefing-metric-label"><span>분석 완료 이슈</span></div><strong>{statValue(formatNumber(selectedStoryCount))}<small>건</small></strong><p>위험 여부를 판정한 뉴스 이슈</p></article>
        <article className="briefing-metric"><div className="briefing-metric-label"><span>부정 이슈 비율</span></div><strong>{statValue(selectedStoryCount ? formatPercent(selectedNegativeCount / selectedStoryCount) : "—")}</strong><p>{statisticsReady ? `부정 감성으로 분석된 이슈 ${formatNumber(selectedNegativeCount)}건` : "분석 결과를 확인하고 있습니다"}</p></article>
        <article className="briefing-metric"><div className="briefing-metric-label"><span>위험 이슈 비율</span></div><strong>{statValue(selectedStoryCount ? formatPercent(selectedRiskCount / selectedStoryCount) : "—")}</strong><p>{statisticsReady ? `위험으로 판정된 이슈 ${formatNumber(selectedRiskCount)}건` : "분석 결과를 확인하고 있습니다"}</p></article>
        <article className="briefing-metric highlighted"><div className="briefing-metric-label"><span>위험 사건</span></div><strong>{eventsReady ? formatNumber(riskTotal) : "—"}<small>건</small></strong><p>사건별 근거와 대응방안 확인</p></article>
      </section>
      <div className="briefing-grid">
        <section className="panel briefing-risk-articles" aria-labelledby="briefing-events-title">
          <header className="briefing-section-head"><div><h2 id="briefing-events-title">위험 사건 및 대응방안 <span className="briefing-count">{eventsReady ? formatNumber(riskTotal) : "—"}</span></h2></div></header>
          <div className="briefing-risk-list">{!riskPageData && !riskPageError ? <p className="panel-empty" role="status">선택한 기간의 위험 사건을 불러오는 중입니다.</p> : riskPageError && !riskPageData ? <p className="panel-empty">선택한 기간의 위험 사건을 불러오지 못했습니다.</p> : riskyStories.length ? riskyStories.map((risk, index) => <details className="briefing-risk-card" key={risk.id} open={expandedRiskId === risk.id}>
            <summary aria-expanded={expandedRiskId === risk.id} aria-controls={`briefing-risk-response-${risk.id}`} onClick={(event) => { event.preventDefault(); setExpandedRiskId((current) => current === risk.id ? null : risk.id); }}>
              <span className="briefing-risk-number" aria-hidden="true">{String((riskPage - 1) * RISK_PAGE_SIZE + index + 1).padStart(2, "0")}</span>
              <strong className="briefing-risk-story-title">{riskEventTitle(risk)}</strong>
            </summary>
            <div className="briefing-risk-response" id={`briefing-risk-response-${risk.id}`}>
              <span className="briefing-response-label">대응방안</span>
              {risk.response_points?.length ? <ul className="briefing-response-points">{risk.response_points.map((point, index) => <li key={`${risk.id}-point-${index}`}>{point}</li>)}</ul> : <p className="briefing-response-summary">{risk.response_summary}</p>}
              <div className="briefing-response-foot"><button type="button" className="briefing-response-more" onClick={() => onOpenCompany(selectedCompanyId, risk.id, { stage: "response" })}>대응 화면에서 보기 <span aria-hidden="true">→</span></button></div>
            </div>
          </details>) : <div className="briefing-event-empty"><span aria-hidden="true">✓</span><h3>이 기간에 확인된 위험 사건이 없습니다</h3><p>다른 기간을 선택하거나 분석 현황을 확인해 보세요.</p></div>}</div>
          <Pagination page={riskPage} pageSize={RISK_PAGE_SIZE} total={riskTotal} onChange={setRiskPage} />
          <div className="briefing-panel-foot"><span>선택한 기업 · {periodLabel}</span><button type="button" onClick={() => onOpenCompany(selectedCompanyId, null, { stage: "risk" })}>위험 분석으로 이동 <span aria-hidden="true">↗</span></button></div>
        </section>
        <section className="panel briefing-overview-panel" aria-labelledby="briefing-analysis-title">
          <header className="briefing-section-head"><div><h2 id="briefing-analysis-title">이슈 분포와 추이</h2></div></header>
          <div className="briefing-overview-head">
            <div className="briefing-view-tabs" role="tablist" aria-label="브리핑 비교 기준">
              <button id="briefing-company-tab" type="button" role="tab" aria-selected={briefingView === "company"} aria-controls="briefing-overview-charts" className={briefingView === "company" ? "active" : ""} onClick={() => setBriefingView("company")}>{selectedCompany.company_role === "main" ? "나의 기업" : "비교 기업"}</button>
              <button id="briefing-average-tab" type="button" role="tab" aria-selected={briefingView === "average"} aria-controls="briefing-overview-charts" className={briefingView === "average" ? "active" : ""} onClick={() => setBriefingView("average")}>전체 평균</button>
            </div>
            <span className="briefing-comparison-note">{briefingView === "average" ? `등록 기업 ${formatNumber(companies.length)}곳 기준` : "선택한 기업 기준"}</span>
          </div>
          {dailyLoading ? <p className="panel-empty" role="status">선택한 기간의 데이터를 불러오는 중입니다.</p> : dailyError ? <p className="panel-empty">선택한 기간의 데이터를 불러오지 못했습니다.</p> : <div id="briefing-overview-charts" className="briefing-overview-charts" role="tabpanel" aria-labelledby={`briefing-${briefingView}-tab`}>
            <section className="briefing-ratio-pane" aria-label="위험 및 감성 비율">
              <div className="briefing-ratio-head"><div className="briefing-ratio-tabs" role="tablist" aria-label="비율 종류">
                <button id="briefing-risk-ratio-tab" type="button" role="tab" aria-controls="briefing-ratio-content" aria-selected={ratioView === "risk"} className={ratioView === "risk" ? "active" : ""} onClick={() => setRatioView("risk")}>위험 비율</button>
                <button id="briefing-sentiment-ratio-tab" type="button" role="tab" aria-controls="briefing-ratio-content" aria-selected={ratioView === "sentiment"} className={ratioView === "sentiment" ? "active" : ""} onClick={() => setRatioView("sentiment")}>부정 비율</button>
              </div></div>
              <div id="briefing-ratio-content" className="briefing-ratio-content" role="tabpanel" aria-labelledby={`briefing-${ratioView}-ratio-tab`}>
                {ratioView === "risk" ? <RiskRatioCard periodLabel={periodLabel} {...selectedRiskRatio} /> : <SentimentRatioCard periodLabel={periodLabel} {...selectedSentimentRatio} />}
              </div>
            </section>
            <section className="briefing-trend-pane" aria-label={`${periodLabel} 위험 및 부정 비율 추이`}>
              <RiskOverviewTrendChart legendTitle="날짜별 변화" days={briefingSummaries} displayDates={trendDisplayDates} basis="stories" ariaLabel={briefingView === "average" ? `등록 기업 전체의 ${periodLabel} 평균 위험 이슈와 부정 이슈 비율` : `${selectedCompany.name} ${periodLabel} 위험 이슈와 부정 이슈 비율`} />
            </section>
          </div>}
        </section>
      </div>
      <footer className="briefing-page-foot"><p>실시간으로 수집한 기사를 모델이 분석하고, AI가 위험 여부와 유형을 분류·판단한 결과입니다.</p></footer>
    </>}
  </section>;
}
