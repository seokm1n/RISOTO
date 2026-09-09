import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { Pagination } from "../../shared/components";
import Icon from "../../shared/Icon";
import {
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

function ChartTooltip({ tip }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    if (!tip || !ref.current) return;
    const box = ref.current.getBoundingClientRect();
    const left = Math.max(8, Math.min(tip.x + 14, window.innerWidth - box.width - 8));
    const top = tip.y + box.height + 20 > window.innerHeight
      ? Math.max(8, tip.y - box.height - 12) : tip.y + 16;
    ref.current.style.left = `${left}px`;
    ref.current.style.top = `${top}px`;
  }, [tip]);
  return tip ? createPortal(<div ref={ref} className="bf-chart-tooltip" role="tooltip">{tip.text}</div>, document.body) : null;
}

function useChartTooltip() {
  const [tip, setTip] = useState(null);
  useEffect(() => {
    const hide = () => setTip(null);
    const onKeyDown = (event) => { if (event.key === "Escape") hide(); };
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);
  const bind = (text) => ({
    tabIndex: 0,
    onPointerEnter: (event) => setTip({ text, x: event.clientX, y: event.clientY }),
    onPointerMove: (event) => setTip({ text, x: event.clientX, y: event.clientY }),
    onPointerLeave: () => setTip(null),
    onFocus: (event) => {
      const box = event.currentTarget.getBoundingClientRect();
      setTip({ text, x: box.left + box.width / 2, y: box.top + box.height / 2 });
    },
    onBlur: () => setTip(null),
  });
  return { bind, tooltip: <ChartTooltip tip={tip} /> };
}

// 위험 지수 게이지: 링은 비율(0~100%)로 채우지만, 가운데 큰 숫자는 이슈 "건수"를 보여준다.
function RiskGauge({ ringRatio, compareRingRatio, countValue, nonRiskCount, nonRiskRatio, countUnit = "건", caption, primaryLabel, delta, ready }) {
  const hover = useChartTooltip();
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
  const segmentLabel = (label, count, ratio) => `${primaryLabel} · ${label} ${formatNumber(count)}${countUnit} · ${percentOne(ratio)}`;
  const segmentTooltip = (label, color, colorName, count, ratio) => <>
    <strong className="bf-chart-tooltip-key"><i style={{ background: color }} aria-hidden="true" />{label} · {colorName}</strong>
    <div>{primaryLabel}</div><div>{formatNumber(count)}{countUnit} · {percentOne(ratio)}</div>
  </>;
  const riskHover = ready && outerRatio > 0 ? hover.bind(segmentTooltip("위험", "var(--violet-600)", "진한 보라", countValue, outerRatio)) : {};
  const nonRiskHover = ready && nonRiskRatio > 0 ? hover.bind(segmentTooltip("비위험", "var(--violet-200)", "연한 보라", nonRiskCount, nonRiskRatio)) : {};

  return <><div className="bf-gauge" role="group" aria-label={`${primaryLabel} 위험·비위험 이슈 비율`}>
    <svg viewBox={`0 0 ${size} ${size}`}>
      <circle className="bf-ring-track" cx={size / 2} cy={size / 2} r={outerRadius} aria-hidden="true" />
      {ready && nonRiskRatio > 0 && <circle className="bf-ring-track bf-ring-segment" cx={size / 2} cy={size / 2} r={outerRadius} strokeDasharray={`${outerLength * (1 - outerRatio)} ${outerLength}`} transform={`rotate(${outerRatio * 360 - 90} ${size / 2} ${size / 2})`} role="img" aria-label={segmentLabel("비위험", nonRiskCount, nonRiskRatio)} {...nonRiskHover} />}
      <circle className="bf-ring-value bf-ring-segment" cx={size / 2} cy={size / 2} r={outerRadius} strokeDasharray={`${outerLength * outerRatio} ${outerLength}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} role="img" aria-label={ready ? segmentLabel("위험", countValue, outerRatio) : "분석 수치를 불러오는 중입니다."} {...riskHover} />
      <circle className="bf-ring-track inner" cx={size / 2} cy={size / 2} r={innerRadius} aria-hidden="true" />
      <circle className="bf-ring-compare" cx={size / 2} cy={size / 2} r={innerRadius} strokeDasharray={`${innerLength * innerRatio} ${innerLength}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} aria-hidden="true" />
    </svg>
    <div className="bf-gauge-label">
      <strong>{ready && Number.isFinite(countValue) ? formatNumber(countValue) : "—"}<small>{countUnit}</small></strong>
      <span>{caption}</span>
      {deltaText && <em className={`bf-gauge-delta ${deltaTone}`}><Icon name={deltaTone === "up" ? "trendingUp" : deltaTone === "down" ? "trendingDown" : "minus"} tone="inherit" />{deltaText}</em>}
    </div>
  </div>{hover.tooltip}</>;
}

// 날짜별 위험·부정 비율을 묶음 막대로 그린다. 목업(추이 Panel)의 구조를 따른다.
function TrendBars({ days }) {
  const hover = useChartTooltip();
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
  const trendScrollRef = useRef(null);
  const latestDate = points[points.length - 1]?.date ?? null;
  useEffect(() => {
    if (!points.length) return;
    const latestScroller = trendScrollRef.current;
    if (!latestScroller) return;
    latestScroller.scrollLeft = latestScroller.scrollWidth - latestScroller.clientWidth;
  }, [points.length, latestDate]);
  if (!points.length) return <p className="bf-empty-inline">아직 표시할 날짜별 비율 데이터가 없습니다.</p>;
  const maxRatio = Math.max(...points.flatMap((point) => [point.risk, point.negative]), 0.05);
  const scaleMax = Math.min(Math.max(Math.ceil(maxRatio * 10) * 10, 10), 100);
  const ticks = [scaleMax, Math.round(scaleMax * 2 / 3), Math.round(scaleMax / 3), 0];
  const peak = points.reduce((best, point) => (point.risk > (best?.risk ?? -1) ? point : best), null);
  const height = (ratio) => `${Math.min(ratio / (scaleMax / 100), 1) * 100}%`;
  const chartMinWidth = `${Math.max(points.length * 72 + 56, 320)}px`;
  const columnsStyle = {
    gridTemplateColumns: `repeat(${points.length}, minmax(52px, 1fr))`,
    columnGap: "16px",
  };
  return <>
    <div className="bf-trend-scroll" ref={trendScrollRef}>
      <div className="bf-trend-chart" style={{ minWidth: chartMinWidth }}>
        <div className="bf-trend-y" aria-hidden="true">{ticks.map((tick) => <span key={tick}>{tick}%</span>)}</div>
        <div className="bf-trend-bars" style={columnsStyle}>
          {points.map((point) => <div className={`bf-trend-day${peak?.date === point.date ? " peak" : ""}`} key={point.date} role="img" aria-label={`${shortDateKey(point.date)} 위험 ${percentOne(point.risk)} ${formatNumber(point.riskCount)}건, 부정 ${percentOne(point.negative)} ${formatNumber(point.negativeCount)}건`} {...hover.bind(`${point.date}\n위험 ${percentOne(point.risk)} · ${formatNumber(point.riskCount)}건\n부정 ${percentOne(point.negative)} · ${formatNumber(point.negativeCount)}건`)}>
            <i className="negative" style={{ height: height(point.negative) }} />
            <i className="risk" style={{ height: height(point.risk) }} />
          </div>)}
        </div>
        <div className="bf-trend-x" aria-hidden="true" style={columnsStyle}>{points.map((point) => <span className={peak?.date === point.date ? "peak" : ""} key={point.date}>{shortDateKey(point.date)}</span>)}</div>
      </div>
    </div>
    {hover.tooltip}
  </>;
}

// 로그인 직후 나의 기업과 전체 평균을 비교하고 선택 기간의 위험 사건과 대응을 브리핑한다.
export default function MainPage({ onOpenCompany }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [briefingView, setBriefingView] = useState("company");
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
  }, [selectedCompanyId, period.start, period.end]);

  useEffect(() => {
    setExpandedRiskId(null);
  }, [selectedCompanyId, period.start, period.end, riskPage]);

  const selectCompany = (companyId) => {
    rememberSelectedCompanyId(companyId);
    setBriefingView("company");
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
  const selectedNonRiskCount = sumOf(dailySummaries, "eligible_non_risk_story_count");
  const selectedRiskRatio = ratioOf(selectedRiskCount, selectedStoryCount);
  const previousStoryCount = sumOf(previousSummaries, "eligible_story_count");
  const previousRiskCount = sumOf(previousSummaries, "eligible_risk_story_count");
  const averageRisk = averageCompanyRisk(dailyGroups);

  const isAverageView = briefingView === "average";
  const viewRiskCount = isAverageView ? averageRisk.totals.risk : selectedRiskCount;
  const viewRiskRatio = isAverageView ? averageRisk.ratios.risk : selectedRiskRatio;
  const viewNonRiskRatio = isAverageView ? averageRisk.ratios.nonRisk : ratioOf(selectedNonRiskCount, selectedStoryCount);
  const viewNonRiskCount = isAverageView ? averageRisk.totals.nonRisk : selectedNonRiskCount;

  const statisticsReady = !loading && !dailyLoading && !dailyError && Boolean(selectedCompany);
  const eventsReady = Boolean(riskPageData) && !riskPageLoading && !riskPageError;
  const riskDeltaCount = statisticsReady && previousStoryCount > 0
    ? selectedRiskCount - previousRiskCount : null;
  const companyRoleLabel = selectedCompany?.company_role === "main" ? "나의 기업" : "비교 기업";

  const headline = (() => {
    if (!statisticsReady) return "분석 결과를 불러오고 있습니다.";
    if (isAverageView) {
      if (!averageRisk.totals.story) return "등록 기업의 판정 완료 이슈가 아직 없습니다.";
      const comparison = selectedStoryCount > 0
        ? `${selectedCompany.name}은(는) ${percentOne(selectedRiskRatio)}로 평균보다 ${selectedRiskRatio > averageRisk.ratios.risk ? "높습니다" : selectedRiskRatio < averageRisk.ratios.risk ? "낮습니다" : "같습니다"}.`
        : "";
      const averageHeadline = `등록 기업 ${formatNumber(companies.length)}곳의 평균 위험 이슈 비율은 ${percentOne(averageRisk.ratios.risk)}입니다.`;
      return comparison ? `${averageHeadline}\n${comparison}` : averageHeadline;
    }
    if (!selectedStoryCount) return "이 기간에 위험 여부를 판정한 이슈가 없습니다. 다른 기간을 선택해 보세요.";
    const base = <>이슈 {formatNumber(selectedStoryCount)}건 중 <strong className="bf-headline-risk-count">{formatNumber(selectedRiskCount)}건</strong>이 위험으로 판정됐습니다.</>;
    const diff = previousStoryCount > 0 ? selectedRiskCount - previousRiskCount : null;
    const change = diff === null ? null : diff > 0 ? `지난 기간보다 ${formatNumber(diff)}건 늘었` : diff < 0 ? `지난 기간보다 ${formatNumber(-diff)}건 줄었` : "지난 기간과 같";
    if (change) return <>{base}<br />{change}습니다.</>;
    return base;
  })();

  const legendRows = [
    { key: "risk", label: "위험 판정 이슈", ratio: viewRiskRatio, count: viewRiskCount },
    { key: "normal", label: "비위험 이슈", ratio: viewNonRiskRatio, count: viewNonRiskCount },
  ];
  const trendPeak = briefingSummaries
    .filter((day) => (day.eligible_story_count ?? 0) > 0)
    .map((day) => ({ date: day.summary_date, risk: Number.isFinite(day.eligible_risk_story_ratio) ? day.eligible_risk_story_ratio : ratioOf(day.eligible_risk_story_count ?? 0, day.eligible_story_count ?? 0) }))
    .sort((left, right) => right.risk - left.risk)[0] ?? null;

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
            <strong>{isAverageView ? "전체 평균" : selectedCompany.name}</strong>
            <span>{isAverageView ? `등록 기업 ${formatNumber(companies.length)}곳 · ${periodShortLabel}` : `${companyRoleLabel} · ${shortDateKey(period.end)} 기준`}</span>
          </div>
          <RiskGauge
            ready={statisticsReady}
            ringRatio={viewRiskRatio}
            compareRingRatio={isAverageView ? selectedRiskRatio : averageRisk.ratios.risk}
            countValue={viewRiskCount}
            nonRiskCount={viewNonRiskCount}
            nonRiskRatio={viewNonRiskRatio}
            caption="위험 판정 이슈"
            primaryLabel={isAverageView ? "전체 평균" : selectedCompany.name}
            delta={isAverageView ? null : riskDeltaCount}
          />
          <div className="bf-compare" aria-label="위험 이슈 비율 비교">
            <span><i className="me" />{isAverageView ? "전체 평균" : "내 기업"}<b>{statisticsReady ? percentOne(viewRiskRatio) : "—"}</b></span>
            <span><i className="avg" />{isAverageView ? selectedCompany.name : "전체 평균"}<b>{statisticsReady ? percentOne(isAverageView ? selectedRiskRatio : averageRisk.ratios.risk) : "—"}</b></span>
          </div>

        </div>
        <div className="bf-story">
          <div className="bf-period-context">
            <span className="bf-badge soft-primary">{periodShortLabel} · {periodDays}일</span>
            {!isAverageView && <span className="bf-previous-period">지난 기간: {previousStart} ~ {previousEnd}<small>선택 기간 바로 이전의 동일한 {periodDays}일 구간과 비교합니다.</small></span>}
          </div>
          <h2 className="bf-headline">{headline}</h2>
          <div className="bf-legend">
            {legendRows.map((row) => <div className={`bf-legend-row ${row.key}`} key={row.key} title={`${row.label} · ${statisticsReady ? `${formatNumber(Math.round(row.count))}건 · ${percentOne(row.ratio)}` : "수치를 불러오는 중입니다."}`}>
              <div className="bf-legend-top"><span><i />{row.label}</span><span className="bf-legend-value"><strong>{statisticsReady ? `${formatNumber(Math.round(row.count))}건` : "—"}</strong><small>{statisticsReady ? percentOne(row.ratio) : ""}</small></span></div>
              <div className="bf-legend-bar"><i style={{ width: `${statisticsReady ? Math.min(row.ratio, 1) * 100 : 0}%` }} /></div>
            </div>)}
          </div>

        </div>
      </section>

      <div className="bf-grid">
        <section className="bf-panel bf-events" aria-labelledby="briefing-events-title">
          <header className="bf-panel-head">
            <div>
              <h2 id="briefing-events-title">위험 이슈 • 대응 <span className="bf-badge primary">{eventsReady ? formatNumber(riskTotal) : "—"}</span></h2>
              <p>확률이 높은 순서. 카드를 열면 대응 초안을 볼 수 있습니다.</p>
            </div>
            <button type="button" className="bf-button link" onClick={() => onOpenCompany(selectedCompanyId, null, { stage: "risk" })}>전체 보기 <Icon name="arrowRight" tone="inherit" /></button>
          </header>
          <div className="bf-event-list">{!riskPageData && !riskPageError ? <p className="bf-empty-inline" role="status">선택한 기간의 위험 사건을 불러오는 중입니다.</p> : riskPageError && !riskPageData ? <p className="bf-empty-inline">선택한 기간의 위험 사건을 불러오지 못했습니다.</p> : riskyStories.length ? riskyStories.map((risk, index) => {
            const open = expandedRiskId === risk.id;
            const severity = SEVERITY_META[risk.severity] ?? { label: risk.severity ?? "판정", className: "neutral" };
            const probability = Number.isFinite(risk.risk_probability) ? `${Math.round(risk.risk_probability * 100)}%` : "—";
            return <details className={`bf-event${open ? " open" : ""}${index === 0 && riskPage === 1 ? " lead" : ""}`} key={risk.id} open={open}>
              <summary aria-expanded={open} aria-controls={`briefing-risk-response-${risk.id}`} onClick={(event) => { event.preventDefault(); setExpandedRiskId((current) => (current === risk.id ? null : risk.id)); }}>
                <span className={`bf-prob ${severity.className}`}><strong>{probability}</strong><small>{risk.severity === "critical" ? "긴급" : "위험"}</small></span>
                <span className="bf-event-text">
                  <strong className="bf-event-title">{riskEventTitle(risk)}</strong>
                  <span className="bf-event-meta">
                    <span className={`bf-badge severity-${severity.className}`}>{severity.label}</span>
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
          
        </section>
      </div>
      <footer className="bf-page-foot"><p>실시간으로 수집한 기사를 모델이 분석하고, AI가 위험 여부와 유형을 분류·판단한 결과입니다.</p></footer>
    </>}
  </section>;
}
