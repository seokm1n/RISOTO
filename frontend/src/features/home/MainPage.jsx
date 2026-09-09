import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { Pagination } from "../../shared/components";
import Icon from "../../shared/Icon";
import {
  RISK_TYPE_LABELS,
  formatNumber,
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
  not_applicable: "상세 판정 결과 대응이 필요한 위험으로 분류되지 않아 대응방안이 생성되지 않았습니다.",
};

const textValue = (...values) => values.find((value) => typeof value === "string" && value.trim())?.trim() ?? null;

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

const SEVERITY_META = {
  critical: { label: "긴급", className: "critical" },
  warning: { label: "주의", className: "warning" },
};

const shortDate = (value) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")}`;
};
const shortDateKey = (key) => (typeof key === "string" && key.length >= 10 ? key.slice(5, 10).replace("-", ".") : "-");
const shiftDateKey = (key, days) => {
  const [year, month, day] = String(key).split("-").map(Number);
  const date = new Date(Date.UTC(year, (month || 1) - 1, day || 1));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
};
const dayCount = (start, end) => {
  const from = new Date(`${start}T00:00:00Z`).getTime();
  const to = new Date(`${end}T00:00:00Z`).getTime();
  return Number.isFinite(from) && Number.isFinite(to) ? Math.max(Math.round((to - from) / 86400000) + 1, 1) : 0;
};
const percentOne = (ratio) => (Number.isFinite(ratio) ? `${(ratio * 100).toFixed(1)}%` : "—");
const ratioOf = (numerator, denominator) => (denominator > 0 ? Math.min(Math.max(numerator / denominator, 0), 1) : 0);

// 위험 지수 게이지: 링은 비율(0~100%)로 채우지만, 가운데 큰 숫자는 이슈 "건수"를 보여준다.
function RiskGauge({ ringRatio, compareRingRatio, countValue, countUnit = "건", caption, delta, ready }) {
  const size = 240;
  const outerRadius = 92;
  const innerRadius = 64;
  const outerLength = 2 * Math.PI * outerRadius;
  const innerLength = 2 * Math.PI * innerRadius;
  const outerRatio = ready && Number.isFinite(ringRatio) ? Math.min(Math.max(ringRatio, 0), 1) : 0;
  const innerRatio = ready && Number.isFinite(compareRingRatio) ? Math.min(Math.max(compareRingRatio, 0), 1) : 0;
  const deltaTone = delta === null || delta === undefined || delta === 0 ? "flat" : delta > 0 ? "up" : "down";
  const deltaText = !ready || delta === null || delta === undefined ? null
    : deltaTone === "flat" ? "지난 기간과 동일"
      : `지난 기간 대비 ${delta > 0 ? "+" : "−"}${formatNumber(Math.abs(delta))}건`;

  return <div className="bf-gauge" role="img" aria-label={`${caption} ${ready && Number.isFinite(countValue) ? formatNumber(countValue) : "정보 없음"}${countUnit}`}>
    <svg viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle className="bf-ring-track" cx={size / 2} cy={size / 2} r={outerRadius} />
      <circle className="bf-ring-value" cx={size / 2} cy={size / 2} r={outerRadius} strokeDasharray={`${outerLength * outerRatio} ${outerLength}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <circle className="bf-ring-track inner" cx={size / 2} cy={size / 2} r={innerRadius} />
      <circle className="bf-ring-compare" cx={size / 2} cy={size / 2} r={innerRadius} strokeDasharray={`${innerLength * innerRatio} ${innerLength}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
    </svg>
    <div className="bf-gauge-label">
      <strong>{ready && Number.isFinite(countValue) ? formatNumber(countValue) : "—"}<small>{countUnit}</small></strong>
      <span>{caption}</span>
      {deltaText && <em className={`bf-gauge-delta ${deltaTone}`}><Icon name={deltaTone === "up" ? "trendingUp" : deltaTone === "down" ? "trendingDown" : "minus"} tone="inherit" />{deltaText}</em>}
    </div>
  </div>;
}

// 날짜별 위험·부정 비율을 묶음 막대로 그린다. 목업(추이 Panel)의 구조를 따른다.
function TrendBars({ days }) {
  const points = [...days]
    .filter((day) => (day.eligible_story_count ?? 0) > 0)
    .sort((left, right) => left.summary_date.localeCompare(right.summary_date))
    .map((day) => {
      const story = Math.max(Number(day.eligible_story_count) || 0, 0);
      const risk = Math.max(Number(day.eligible_risk_story_count) || 0, 0);
      const negative = Math.max(Number(day.eligible_negative_story_count) || 0, 0);
      return {
        date: day.summary_date,
        risk: Number.isFinite(day.eligible_risk_story_ratio) ? Math.min(Math.max(day.eligible_risk_story_ratio, 0), 1) : ratioOf(risk, story),
        negative: Number.isFinite(day.eligible_negative_story_ratio) ? Math.min(Math.max(day.eligible_negative_story_ratio, 0), 1) : ratioOf(negative, story),
        riskCount: risk,
        negativeCount: negative,
      };
    });
  if (!points.length) return <p className="bf-empty-inline">아직 표시할 날짜별 비율 데이터가 없습니다.</p>;
  const maxRatio = Math.max(...points.flatMap((point) => [point.risk, point.negative]), 0.05);
  const scaleMax = Math.min(Math.max(Math.ceil(maxRatio * 10) * 10, 10), 100);
  const ticks = [scaleMax, Math.round(scaleMax * 2 / 3), Math.round(scaleMax / 3), 0];
  const peak = points.reduce((best, point) => (point.risk > (best?.risk ?? -1) ? point : best), null);
  const height = (ratio) => `${Math.min(ratio / (scaleMax / 100), 1) * 100}%`;
  return <>
    <div className="bf-trend-plot">
      <div className="bf-trend-y" aria-hidden="true">{ticks.map((tick) => <span key={tick}>{tick}%</span>)}</div>
      <div className="bf-trend-bars">
        {points.map((point) => <div className={`bf-trend-day${peak?.date === point.date ? " peak" : ""}`} key={point.date} role="img" aria-label={`${shortDateKey(point.date)} 위험 ${percentOne(point.risk)} ${formatNumber(point.riskCount)}건, 부정 ${percentOne(point.negative)} ${formatNumber(point.negativeCount)}건`} title={`${shortDateKey(point.date)} · 위험 ${percentOne(point.risk)} · 부정 ${percentOne(point.negative)}`}>
          <i className="negative" style={{ height: height(point.negative) }} />
          <i className="risk" style={{ height: height(point.risk) }} />
        </div>)}
      </div>
    </div>
    <div className="bf-trend-x" aria-hidden="true">{points.map((point) => <span className={peak?.date === point.date ? "peak" : ""} key={point.date}>{shortDateKey(point.date)}</span>)}</div>
  </>;
}

// 로그인 직후 나의 기업과 등록 기업 평균을 비교하고 선택 기간의 위험 사건과 대응을 브리핑한다.
export default function MainPage({ onOpenCompany }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [briefingView, setBriefingView] = useState("company");
  const { period, changePeriod } = useAnalysisPeriod();
  const [riskPage, setRiskPage] = useState(1);
  const [expandedRiskId, setExpandedRiskId] = useState(null);
  const eventsRef = useRef(null);
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
  }, [selectedCompanyId, period.start, period.end]);

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

  // 지난 기간(같은 길이의 직전 구간)과 비교해 위험 비율 변화를 보여준다.
  const periodDays = dayCount(period.start, period.end);
  const previousEnd = shiftDateKey(period.start, -1);
  const previousStart = shiftDateKey(previousEnd, -(periodDays - 1));
  const { data: previousSummaries = [] } = useSharedResource(
    selectedCompanyId ? `main-briefing-previous:${selectedCompanyId}:${previousStart}:${previousEnd}` : "skip:main-briefing-previous",
    selectedCompanyId
      ? () => api.get(`/companies/${selectedCompanyId}/daily-summaries?start_date=${previousStart}&end_date=${previousEnd}`).then((response) => response.data).catch(() => [])
      : () => Promise.resolve([]),
  );

  const selectedIndex = companies.findIndex((company) => company.id === selectedCompanyId);
  const dailySummaries = selectedIndex >= 0 ? dailyGroups[selectedIndex] ?? [] : [];
  const averageSummaries = averageDailySummaries(dailyGroups);
  const briefingSummaries = briefingView === "average" ? averageSummaries : dailySummaries;

  const riskQueryKey = selectedCompanyId
    ? `main-briefing-risks:judged:${selectedCompanyId}:${period.start}:${period.end}:${riskPage}`
    : "skip:main-briefing-risks";
  const { data: loadedRiskPageData, error: riskPageError, loading: riskPageLoading } = useSharedResource(
    riskQueryKey,
    selectedCompanyId
      ? async () => {
          const params = new URLSearchParams({
            view: "all", page: String(riskPage), page_size: String(RISK_PAGE_SIZE), response: "all",
            start_date: period.start, end_date: period.end,
          });
          const response = await api.get(`/companies/${selectedCompanyId}/risk-events/page?${params}`);
          const pageData = response.data ?? { items: [], total: 0, page: riskPage, page_size: RISK_PAGE_SIZE };
          const items = await Promise.all((pageData.items ?? []).map(async (risk) => {
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
              return { ...risk, response_summary: RESPONSE_STATUS_SUMMARIES[risk.response_generation_status] ?? RESPONSE_STATUS_SUMMARIES.idle };
            }
          }));
          return { ...pageData, items, queryKey: riskQueryKey };
        }
      : () => Promise.resolve({ items: [], total: 0, page: 1, page_size: RISK_PAGE_SIZE, queryKey: riskQueryKey }),
  );
  const riskPageData = loadedRiskPageData?.queryKey === riskQueryKey ? loadedRiskPageData : null;
  const riskyStories = riskPageData?.items ?? [];
  const riskTotal = riskPageData?.total ?? 0;

  useEffect(() => {
    if (riskPageLoading || !riskPageData) return;
    const lastPage = Math.max(1, Math.ceil(riskTotal / RISK_PAGE_SIZE));
    if (riskPage > lastPage) setRiskPage(lastPage);
  }, [riskPage, riskPageData, riskPageLoading, riskTotal]);

  const error = companiesError || riskPageError || dailyError
    ? getErrorMessage(companiesError ?? riskPageError ?? dailyError) : null;
  const periodLabel = `${period.start} ~ ${period.end}`;
  const periodShortLabel = `${shortDateKey(period.start)} – ${shortDateKey(period.end)}`;

  const sumOf = (days, key) => days.reduce((sum, day) => sum + Math.max(Number(day[key]) || 0, 0), 0);
  const selectedStoryCount = sumOf(dailySummaries, "eligible_story_count");
  const selectedRiskCount = sumOf(dailySummaries, "eligible_risk_story_count");
  const selectedNegativeCount = sumOf(dailySummaries, "eligible_negative_story_count");
  const selectedRiskRatio = ratioOf(selectedRiskCount, selectedStoryCount);
  const previousStoryCount = sumOf(previousSummaries, "eligible_story_count");
  const previousRiskCount = sumOf(previousSummaries, "eligible_risk_story_count");
  const averageRisk = averageCompanyRisk(dailyGroups);
  const averageSentiment = averageCompanySentiment(dailyGroups);

  const isAverageView = briefingView === "average";
  const viewStoryCount = isAverageView ? averageRisk.totals.story : selectedStoryCount;
  const viewRiskCount = isAverageView ? averageRisk.totals.risk : selectedRiskCount;
  const viewNegativeCount = isAverageView ? averageSentiment.totals.negative : selectedNegativeCount;
  const viewRiskRatio = isAverageView ? averageRisk.ratios.risk : selectedRiskRatio;
  const viewNegativeRatio = isAverageView ? averageSentiment.ratios.negative : ratioOf(selectedNegativeCount, selectedStoryCount);
  const negativeOnlyRatio = Math.max(viewNegativeRatio - viewRiskRatio, 0);
  const normalRatio = Math.max(1 - viewRiskRatio - negativeOnlyRatio, 0);
  const negativeOnlyCount = Math.max(viewNegativeCount - viewRiskCount, 0);
  const normalCount = Math.max(viewStoryCount - viewRiskCount - negativeOnlyCount, 0);

  const statisticsReady = !loading && !dailyLoading && !dailyError && Boolean(selectedCompany);
  const eventsReady = Boolean(riskPageData) && !riskPageLoading && !riskPageError;
  const riskDeltaCount = statisticsReady && previousStoryCount > 0
    ? selectedRiskCount - previousRiskCount : null;
  const companyRoleLabel = selectedCompany?.company_role === "main" ? "나의 기업" : "비교 기업";

  const typeCounts = riskyStories.reduce((counts, risk) => {
    const key = risk.primary_type ?? risk.risk_types?.[0]?.type ?? risk.risk_types?.[0]?.code ?? null;
    if (key) counts.set(key, (counts.get(key) ?? 0) + 1);
    return counts;
  }, new Map());
  const topType = [...typeCounts.entries()].sort((left, right) => right[1] - left[1])[0]?.[0] ?? null;
  const topTypeLabel = topType ? RISK_TYPE_LABELS[topType] ?? topType : null;

  const headline = (() => {
    if (!statisticsReady) return "분석 결과를 불러오고 있습니다.";
    if (isAverageView) {
      if (!averageRisk.totals.story) return "등록 기업의 판정 완료 이슈가 아직 없습니다.";
      const comparison = selectedStoryCount > 0
        ? ` ${selectedCompany.name}은(는) ${percentOne(selectedRiskRatio)}로 평균보다 ${selectedRiskRatio > averageRisk.ratios.risk ? "높습니다" : selectedRiskRatio < averageRisk.ratios.risk ? "낮습니다" : "같습니다"}.`
        : "";
      return `등록 기업 ${formatNumber(companies.length)}곳의 평균 위험 이슈 비율은 ${percentOne(averageRisk.ratios.risk)}입니다.${comparison}`;
    }
    if (!selectedStoryCount) return "이 기간에 위험 여부를 판정한 이슈가 없습니다. 다른 기간을 선택해 보세요.";
    const base = `이슈 ${formatNumber(selectedStoryCount)}건 중 ${formatNumber(selectedRiskCount)}건이 위험으로 판정됐습니다.`;
    const diff = previousStoryCount > 0 ? selectedRiskCount - previousRiskCount : null;
    const change = diff === null ? null : diff > 0 ? `지난 기간보다 ${formatNumber(diff)}건 늘었` : diff < 0 ? `지난 기간보다 ${formatNumber(-diff)}건 줄었` : "지난 기간과 같";
    if (change && topTypeLabel) return `${base} ${change}고, 대부분 ${topTypeLabel} 유형입니다.`;
    if (change) return `${base} ${change}습니다.`;
    if (topTypeLabel) return `${base} 대부분 ${topTypeLabel} 유형입니다.`;
    return base;
  })();

  const legendRows = [
    { key: "risk", label: "위험 판정 이슈", ratio: viewRiskRatio, count: viewRiskCount },
    { key: "negative", label: "부정 감성 이슈 (위험 제외)", ratio: negativeOnlyRatio, count: negativeOnlyCount },
    { key: "normal", label: "정상 이슈", ratio: normalRatio, count: normalCount },
  ];
  const trendPeak = briefingSummaries
    .filter((day) => (day.eligible_story_count ?? 0) > 0)
    .map((day) => ({ date: day.summary_date, risk: Number.isFinite(day.eligible_risk_story_ratio) ? day.eligible_risk_story_ratio : ratioOf(day.eligible_risk_story_count ?? 0, day.eligible_story_count ?? 0) }))
    .sort((left, right) => right.risk - left.risk)[0] ?? null;

  const scrollToEvents = () => eventsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });

  return <section className="workspace main-workspace briefing-workspace">
    <header className="bf-head">
      <div className="bf-head-copy">
        <nav className="bf-crumb" aria-label="현재 위치"><span>Briefing</span><Icon name="chevronRight" tone="inherit" /><strong>{selectedCompany ? `${selectedCompany.name} · ${companyRoleLabel}` : "기업 선택"}</strong></nav>
        <h1>리스크 브리핑</h1>
      </div>
      <div className="bf-filters">
        <label className="bf-select" htmlFor="briefing-company">
          <Icon name="search" tone="inherit" />
          <span className="sr-only">분석 기업</span>
          <select id="briefing-company" value={selectedCompanyId ? String(selectedCompanyId) : ""} onChange={(event) => selectCompany(event.target.value)} disabled={loading || !companies.length}><option value="" disabled>{loading ? "불러오는 중..." : "기업을 선택하세요"}</option>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="비교 기업">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select>
          <Icon name="chevronDown" tone="inherit" className="bf-select-chevron" />
        </label>
        <AnalysisPeriodControl period={period} onChange={(field, value) => { setRiskPage(1); changePeriod(field, value); }} />
        {selectedCompany && <div className="bf-tabs" role="tablist" aria-label="브리핑 비교 기준">
          <button id="briefing-company-tab" type="button" role="tab" aria-selected={!isAverageView} aria-controls="briefing-hero" className={!isAverageView ? "active" : ""} onClick={() => setBriefingView("company")}>{companyRoleLabel}</button>
          <button id="briefing-average-tab" type="button" role="tab" aria-selected={isAverageView} aria-controls="briefing-hero" className={isAverageView ? "active" : ""} onClick={() => setBriefingView("average")}>전체 평균</button>
        </div>}
      </div>
    </header>
    {error && <div className="notice error bf-notice" role="alert">{error}</div>}
    {loading ? <div className="bf-state" role="status"><span className="bf-state-mark" aria-hidden="true">···</span><h2>브리핑을 준비하고 있습니다</h2><p>기업의 분석 결과를 불러오는 중입니다.</p></div> : !selectedCompany ? <div className="bf-state"><span className="bf-state-mark" aria-hidden="true">+</span><h2>{companiesError ? "기업 정보를 불러오지 못했습니다" : "모니터링할 기업을 등록해 주세요"}</h2><p>{companiesError ? "잠시 후 화면을 새로고침해 다시 확인해 주세요." : "기업을 등록하면 수집한 뉴스와 위험 분석 결과를 이곳에서 확인할 수 있습니다."}</p>{!companiesError && <Link to="/companies" className="bf-button primary">기업 등록하기 <Icon name="arrowRight" tone="inherit" /></Link>}</div> : <>
      <section id="briefing-hero" className="bf-hero" role="tabpanel" aria-labelledby={isAverageView ? "briefing-average-tab" : "briefing-company-tab"} aria-busy={dailyLoading}>
        <div className="bf-gauge-side">
          <div className="bf-gauge-company">
            <strong>{isAverageView ? "등록 기업 평균" : selectedCompany.name}</strong>
            <span>{isAverageView ? `등록 기업 ${formatNumber(companies.length)}곳 · ${periodShortLabel}` : `${companyRoleLabel} · ${shortDateKey(period.end)} 기준`}</span>
          </div>
          <RiskGauge
            ready={statisticsReady}
            ringRatio={viewRiskRatio}
            compareRingRatio={isAverageView ? selectedRiskRatio : averageRisk.ratios.risk}
            countValue={viewRiskCount}
            caption="위험 판정 이슈"
            delta={isAverageView ? null : riskDeltaCount}
          />
          <div className="bf-compare" aria-label="위험 이슈 비율 비교">
            <span><i className="me" />{isAverageView ? "등록 기업 평균" : "내 기업"}<b>{statisticsReady ? percentOne(viewRiskRatio) : "—"}</b></span>
            <span><i className="avg" />{isAverageView ? selectedCompany.name : "등록 기업 평균"}<b>{statisticsReady ? percentOne(isAverageView ? selectedRiskRatio : averageRisk.ratios.risk) : "—"}</b></span>
          </div>
          <div className="bf-tiles">
            <div className="danger"><strong>{eventsReady ? `${formatNumber(riskTotal)}건` : "—"}</strong><span>위험 사건</span></div>
            <div className="warning"><strong>{statisticsReady ? `${formatNumber(viewNegativeCount)}건` : "—"}</strong><span>부정 이슈</span></div>
            <div><strong>{statisticsReady ? `${formatNumber(viewStoryCount)}건` : "—"}</strong><span>분석 완료 이슈</span></div>
          </div>
        </div>
        <div className="bf-story">
          <span className="bf-badge soft-primary">{periodShortLabel} · {periodDays}일</span>
          <h2 className="bf-headline">{headline}</h2>
          <div className="bf-legend">
            {legendRows.map((row) => <div className={`bf-legend-row ${row.key}`} key={row.key}>
              <div className="bf-legend-top"><span><i />{row.label}</span><span className="bf-legend-value"><strong>{statisticsReady ? `${formatNumber(Math.round(row.count))}건` : "—"}</strong><small>{statisticsReady ? percentOne(row.ratio) : ""}</small></span></div>
              <div className="bf-legend-bar"><i style={{ width: `${statisticsReady ? Math.min(row.ratio, 1) * 100 : 0}%` }} /></div>
            </div>)}
          </div>
          <div className="bf-story-foot">
            <button type="button" className="bf-button primary" onClick={scrollToEvents}><Icon name="siren" tone="inherit" />위험 사건 {eventsReady ? formatNumber(riskTotal) : "—"}건 확인</button>
            <button type="button" className="bf-button ghost" onClick={() => onOpenCompany(selectedCompanyId, null, { stage: "risk" })}><Icon name="gitBranch" tone="inherit" />분석 파이프라인 열기</button>
          </div>
        </div>
      </section>

      <div className="bf-grid">
        <section className="bf-panel bf-events" aria-labelledby="briefing-events-title" ref={eventsRef}>
          <header className="bf-panel-head">
            <div>
              <h2 id="briefing-events-title">위험 사건 및 대응방안 <span className="bf-badge primary">{eventsReady ? formatNumber(riskTotal) : "—"}</span></h2>
              <p>확률이 높은 순서. 카드를 열면 대응 초안을 볼 수 있습니다.</p>
            </div>
            <button type="button" className="bf-button link" onClick={() => onOpenCompany(selectedCompanyId, null, { stage: "risk" })}>전체 보기 <Icon name="arrowRight" tone="inherit" /></button>
          </header>
          <div className="bf-event-list">{!riskPageData && !riskPageError ? <p className="bf-empty-inline" role="status">선택한 기간의 위험 사건을 불러오는 중입니다.</p> : riskPageError && !riskPageData ? <p className="bf-empty-inline">선택한 기간의 위험 사건을 불러오지 못했습니다.</p> : riskyStories.length ? riskyStories.map((risk, index) => {
            const open = expandedRiskId === risk.id;
            const severity = SEVERITY_META[risk.severity] ?? { label: risk.severity ?? "판정", className: "neutral" };
            const typeLabel = RISK_TYPE_LABELS[risk.primary_type] ?? risk.risk_types?.[0]?.label ?? risk.primary_type ?? "유형 분류 중";
            const probability = Number.isFinite(risk.risk_probability) ? `${Math.round(risk.risk_probability * 100)}%` : "—";
            return <details className={`bf-event${open ? " open" : ""}${index === 0 && riskPage === 1 ? " lead" : ""}`} key={risk.id} open={open}>
              <summary aria-expanded={open} aria-controls={`briefing-risk-response-${risk.id}`} onClick={(event) => { event.preventDefault(); setExpandedRiskId((current) => (current === risk.id ? null : risk.id)); }}>
                <span className={`bf-prob ${severity.className}`}><strong>{probability}</strong><small>{risk.severity === "critical" ? "긴급" : "위험"}</small></span>
                <span className="bf-event-text">
                  <strong className="bf-event-title">{riskEventTitle(risk)}</strong>
                  <span className="bf-event-meta">
                    <span className={`bf-badge severity-${severity.className}`}>{severity.label}</span>
                    <span className="bf-event-type">{typeLabel}</span>
                    <span className="bf-event-sub">{shortDate(risk.last_evidence_at ?? risk.issue_latest_at ?? risk.detected_at)} · 기사 {formatNumber(risk.risk_article_count || risk.evidence_article_count)} · 언론사 {formatNumber(risk.risk_source_count || risk.source_count)}</span>
                  </span>
                </span>
                <Icon name="chevronRight" tone="inherit" className="bf-event-chevron" />
              </summary>
              <div className="bf-event-response" id={`briefing-risk-response-${risk.id}`}>
                <span className="bf-response-label">대응방안</span>
                {risk.response_points?.length ? <ul className="bf-response-points">{risk.response_points.map((point, pointIndex) => <li key={`${risk.id}-point-${pointIndex}`}>{point}</li>)}</ul> : <p className="bf-response-summary">{risk.response_summary}</p>}
                <div className="bf-response-foot"><button type="button" className="bf-button outline small" onClick={() => onOpenCompany(selectedCompanyId, risk.id, { stage: "response" })}>대응 화면에서 보기 <Icon name="arrowRight" tone="inherit" /></button></div>
              </div>
            </details>;
          }) : <div className="bf-event-empty"><span aria-hidden="true"><Icon name="check" tone="inherit" /></span><h3>이 기간에 확인된 위험 사건이 없습니다</h3><p>다른 기간을 선택하거나 분석 현황을 확인해 보세요.</p></div>}</div>
          <footer className="bf-panel-foot">
            <span>{isAverageView ? "위험 사건은 선택한 기업 기준" : selectedCompany.name} · {periodLabel}</span>
            <Pagination page={riskPage} pageSize={RISK_PAGE_SIZE} total={riskTotal} onChange={setRiskPage} />
          </footer>
        </section>

        <section className="bf-panel bf-trend" aria-label={`${periodLabel} 위험 및 부정 비율 추이`}>
          <header className="bf-panel-head">
            <div>
              <h2>날짜별 위험·부정 비율</h2>
              <p>{dailyLoading ? "데이터를 불러오는 중입니다." : trendPeak ? `${shortDateKey(trendPeak.date)}에 위험 비율 ${percentOne(trendPeak.risk)}로 정점` : "표시할 날짜별 데이터가 없습니다."}</p>
            </div>
            <div className="bf-trend-legend" aria-hidden="true"><span><i className="negative" />부정</span><span><i className="risk" />위험</span></div>
          </header>
          {dailyLoading ? <p className="bf-empty-inline" role="status">선택한 기간의 데이터를 불러오는 중입니다.</p> : dailyError ? <p className="bf-empty-inline">선택한 기간의 데이터를 불러오지 못했습니다.</p> : <TrendBars days={briefingSummaries} />}
          <div className="bf-note"><Icon name="lightbulb" tone="inherit" /><div><strong>위험은 부정의 부분집합입니다</strong><p>부정 감성 이슈 중 사건화 기준(정제 기사 2건 이상)을 넘긴 것만 위험으로 셉니다.</p></div></div>
        </section>
      </div>
      <footer className="bf-page-foot"><p>실시간으로 수집한 기사를 모델이 분석하고, AI가 위험 여부와 유형을 분류·판단한 결과입니다.</p></footer>
    </>}
  </section>;
}
