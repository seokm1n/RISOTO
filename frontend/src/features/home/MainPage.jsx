import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { seoulDateKey } from "../../shared/briefingPeriodSession";
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
import CompanySearchField from "../../shared/CompanySearchField";

const RISK_FETCH_SIZE = 100;
const LIST_VISIBLE_COUNT = 4;

const SEVERITY_LABEL = { critical: "긴급", warning: "주의", watch: "관찰" };
const severityBandOf = (risk) => risk?.severity === "critical" ? "critical"
  : risk?.severity === "warning" ? "warning" : "watch";

const RESPONSE_STATUS_LABELS = {
  pending: "생성 중", generating: "생성 중", generated: "생성 완료",
  deferred: "보류", failed: "실패", idle: "미생성",
};

function shortDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return `${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")}`;
}

// 같은 길이의 바로 이전 기간을 계산한다. 다른 곳(AnalysisPipelinePage)과 같은
// +09:00 고정 오프셋으로 날짜 경계를 맞춘다.
function previousPeriodOf(period) {
  const start = new Date(`${period.start}T00:00:00+09:00`);
  const end = new Date(`${period.end}T00:00:00+09:00`);
  const prevEnd = new Date(start.getTime() - 24 * 60 * 60 * 1000);
  const prevStart = new Date(prevEnd.getTime() - (end.getTime() - start.getTime()));
  return { start: seoulDateKey(prevStart), end: seoulDateKey(prevEnd) };
}

// 여러 기업의 일별 요약을 날짜 기준으로 합산해 "등록 기업 전체 평균" 추이를 만든다.
function averageDailySummaries(groups) {
  if (!groups.length) return [];
  const oneDecimal = (value) => Math.round((value / groups.length) * 10) / 10;
  const byDate = new Map();
  groups.flat().forEach((day) => {
    const current = byDate.get(day.summary_date) ?? {
      summary_date: day.summary_date, eligible_story_count: 0, eligible_risk_story_count: 0,
      eligible_negative_story_count: 0,
    };
    current.eligible_story_count += day.eligible_story_count ?? 0;
    current.eligible_risk_story_count += day.eligible_risk_story_count ?? 0;
    current.eligible_negative_story_count += day.eligible_negative_story_count ?? 0;
    byDate.set(day.summary_date, current);
  });
  return [...byDate.values()].map((day) => ({
    ...day,
    eligible_story_count: oneDecimal(day.eligible_story_count),
    eligible_risk_story_count: oneDecimal(day.eligible_risk_story_count),
    eligible_negative_story_count: oneDecimal(day.eligible_negative_story_count),
  }));
}

function CalendarIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M8 3v4M16 3v4M3 10h18" /></svg>;
}
function BellIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 15h12l-1-6a5 5 0 0 0-10 0l-1 6Z" /><path d="M4 15h16v3H4z" /><path d="M8 21h8" /></svg>;
}
function ArrowUpRightIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M7 17 17 7M8 7h9v9" /></svg>;
}
function ChevronIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="18" height="18"><path d="m9 6 6 6-6 6" /></svg>;
}
function BulbIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.5.4.8 1 .8 1.7V16h5.6v-.5c0-.7.3-1.3.8-1.7A6 6 0 0 0 12 3Z" /></svg>;
}

function Donut({ segments, total }) {
  const radius = 90;
  const circumference = 2 * Math.PI * radius;
  let cumulative = 0;
  return <svg viewBox="0 0 220 220" role="img" aria-label={`분석 완료 이슈 ${total}건`}>
    <circle className="brief-donut-track" cx="110" cy="110" r={radius} />
    {segments.filter((segment) => segment.value > 0).map((segment) => {
      const ratio = total > 0 ? segment.value / total : 0;
      const dash = `${circumference * ratio} ${circumference}`;
      const offset = -cumulative * circumference;
      cumulative += ratio;
      return <circle
        className={`brief-donut-seg ${segment.key}`} cx="110" cy="110" r={radius}
        stroke={segment.color} strokeDasharray={dash} strokeDashoffset={offset}
        key={segment.key}
      />;
    })}
  </svg>;
}

// 담당자가 화면을 열자마자 알아야 하는 것: 이번 기간 위험 판정이 얼마나 되고,
// 지난 기간과 비교해 어떻게 바뀌었으며, 지금 무엇부터 봐야 하는가. 파이프라인의
// 단계별 수치 대신 이 세 가지만 남긴다.
export default function MainPage({ onOpenRisk }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [briefingView, setBriefingView] = useState("company");
  const { period, changePeriod } = useAnalysisPeriod();
  const [expanded, setExpanded] = useState(false);
  const listRef = useRef(null);

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

  useEffect(() => { setExpanded(false); }, [selectedCompanyId, period.start, period.end]);

  const selectCompany = (companyId) => {
    rememberSelectedCompanyId(companyId);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("companyId", companyId);
      return next;
    });
  };

  const selectMine = () => {
    setBriefingView("company");
    if (selectedCompany?.company_role !== "main" && mainCompanies[0]) selectCompany(mainCompanies[0].id);
  };
  const selectCompetitor = () => {
    if (!competitorCompanies.length) return;
    setBriefingView("company");
    if (selectedCompany?.company_role !== "competitor") selectCompany(competitorCompanies[0].id);
  };
  const selectAverage = () => setBriefingView("average");
  const activeMode = briefingView === "average" ? "average" : selectedCompany?.company_role === "competitor" ? "competitor" : "mine";

  const { data: dailyGroups = [], error: dailyError } = useSharedResource(
    companyIds ? `main-briefing-daily:judged:${companyIds}:${period.start}:${period.end}` : "skip:main-briefing-daily",
    companyIds
      ? () => Promise.all(companies.map((company) => api.get(`/companies/${company.id}/daily-summaries?start_date=${period.start}&end_date=${period.end}`).then((response) => response.data)))
      : () => Promise.resolve([]),
  );
  const selectedIndex = companies.findIndex((company) => company.id === selectedCompanyId);
  const dailySummaries = selectedIndex >= 0 ? dailyGroups[selectedIndex] ?? [] : [];
  const averageSummaries = averageDailySummaries(dailyGroups);
  const briefingSummaries = briefingView === "average" ? averageSummaries : dailySummaries;

  const prevPeriod = previousPeriodOf(period);
  const { data: prevSummaries = [] } = useSharedResource(
    selectedCompanyId && briefingView === "company"
      ? `main-briefing-prev:${selectedCompanyId}:${prevPeriod.start}:${prevPeriod.end}`
      : "skip:main-briefing-prev",
    selectedCompanyId && briefingView === "company"
      ? () => api.get(`/companies/${selectedCompanyId}/daily-summaries?start_date=${prevPeriod.start}&end_date=${prevPeriod.end}`).then((response) => response.data ?? [])
      : () => Promise.resolve([]),
  );

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
  const sortedRiskEvents = [...allRiskEvents].sort((left, right) => (right.risk_probability ?? 0) - (left.risk_probability ?? 0));
  const visibleRiskEvents = expanded ? sortedRiskEvents : sortedRiskEvents.slice(0, LIST_VISIBLE_COUNT);

  const error = companiesError || riskPageError || dailyError
    ? getErrorMessage(companiesError ?? riskPageError ?? dailyError) : null;

  const periodStoryCount = Math.round(briefingSummaries.reduce((sum, day) => sum + (day.eligible_story_count ?? 0), 0));
  const periodRiskCount = Math.round(briefingSummaries.reduce((sum, day) => sum + (day.eligible_risk_story_count ?? 0), 0));
  const periodNegativeCount = Math.round(briefingSummaries.reduce((sum, day) => sum + (day.eligible_negative_story_count ?? 0), 0));
  const negativeExcludingRisk = Math.max(0, periodNegativeCount - periodRiskCount);
  const normalCount = Math.max(0, periodStoryCount - periodRiskCount - negativeExcludingRisk);
  const riskRatio = periodStoryCount > 0 ? periodRiskCount / periodStoryCount : 0;
  const negativeRatio = periodStoryCount > 0 ? negativeExcludingRisk / periodStoryCount : 0;
  const normalRatio = periodStoryCount > 0 ? normalCount / periodStoryCount : 0;

  const prevRiskCount = Math.round(prevSummaries.reduce((sum, day) => sum + (day.eligible_risk_story_count ?? 0), 0));
  const riskDelta = periodRiskCount - prevRiskCount;

  const typeCounts = new Map();
  allRiskEvents.forEach((risk) => {
    const primary = (risk.risk_types ?? []).find((item) => item.is_primary) ?? (risk.risk_types ?? [])[0];
    if (!primary) return;
    typeCounts.set(primary.risk_type, (typeCounts.get(primary.risk_type) ?? 0) + 1);
  });
  const topType = [...typeCounts.entries()].sort((left, right) => right[1] - left[1])[0];
  const topTypeLabel = topType ? RISK_TYPE_LABELS[topType[0]] ?? topType[0] : null;

  const dayCount = briefingSummaries.length || Math.round((new Date(period.end) - new Date(period.start)) / 86400000) + 1;
  const periodLabel = `${period.start.slice(5).replace("-", ".")} – ${period.end.slice(5).replace("-", ".")}`;

  let headline;
  if (!periodStoryCount) {
    headline = "선택한 기간에 판정 가능한 신호가 아직 없습니다.";
  } else if (briefingView === "average") {
    headline = <>등록 기업 평균으로 이슈 <strong>{formatNumber(periodStoryCount)}건</strong> 중 <strong>{formatNumber(periodRiskCount)}건</strong>이 위험 사건으로 분류됐습니다.{topTypeLabel && <> 대부분 <strong>{topTypeLabel}</strong> 유형입니다.</>}</>;
  } else {
    const deltaText = riskDelta === 0 ? "이전 기간과 같고" : riskDelta > 0 ? <>이전 기간보다 <strong>{formatNumber(riskDelta)}건</strong> 늘었고</> : <>이전 기간보다 <strong>{formatNumber(Math.abs(riskDelta))}건</strong> 줄었고</>;
    headline = <>이슈 <strong>{formatNumber(periodStoryCount)}건</strong> 중 <strong>{formatNumber(periodRiskCount)}건</strong>이 위험 사건으로 확정됐습니다. {deltaText}{topTypeLabel && <>, 대부분 <strong>{topTypeLabel}</strong> 유형입니다.</>}</>;
  }

  // 날짜별 막대 그래프: 위험 비율이 가장 높은 날을 찾아 x축 라벨을 강조한다.
  const barDays = [...briefingSummaries].sort((left, right) => left.summary_date.localeCompare(right.summary_date));
  const barPoints = barDays.map((day) => {
    const story = Math.max(Number(day.eligible_story_count) || 0, 0);
    const risk = Math.max(Number(day.eligible_risk_story_count) || 0, 0);
    const negative = Math.max(Number(day.eligible_negative_story_count) || 0, 0);
    const riskRatioDay = story > 0 ? risk / story : 0;
    const negRatioDay = story > 0 ? Math.max(0, negative - risk) / story : 0;
    return { date: day.summary_date, riskRatioDay, negRatioDay };
  });
  const peakDay = barPoints.reduce((best, point) => !best || point.riskRatioDay > best.riskRatioDay ? point : best, null);
  const maxRatio = Math.max(0.1, ...barPoints.map((point) => Math.max(point.riskRatioDay, point.negRatioDay)));
  const axisMax = Math.ceil(maxRatio * 10) / 10;
  const axisTicks = [axisMax, axisMax / 2, 0];

  return <section className="workspace signal-scope brief-dashboard">
    <p className="brief-crumb">Briefing <ChevronIcon /> <b>{selectedCompany ? `${selectedCompany.name} · ${selectedCompany.company_role === "main" ? "나의 기업" : "비교 기업"}` : "기업 미선택"}</b></p>

    <div className="brief-head-row">
      <h1>이번 기간 리스크 브리핑</h1>
      <div className="brief-head-controls">
        <CompanySearchField companies={companies} selectedCompany={selectedCompany} onSelect={selectCompany} />
        <div className="brief-date-pill"><CalendarIcon /><AnalysisPeriodControl period={period} onChange={(field, value) => changePeriod(field, value)} /></div>
        <div className="brief-mode-group" role="group" aria-label="비교 기준">
          <button type="button" className={activeMode === "mine" ? "active" : ""} onClick={selectMine} disabled={!mainCompanies.length}>나의 기업</button>
          <button type="button" className={activeMode === "average" ? "active" : ""} onClick={selectAverage}>전체 평균</button>
          <button type="button" className={activeMode === "competitor" ? "active" : ""} onClick={selectCompetitor} disabled={!competitorCompanies.length}>비교 기업</button>
        </div>
      </div>
    </div>

    {error && <div className="notice error">{error}</div>}
    {loading ? <p className="empty-state">브리핑을 불러오는 중입니다.</p> : !selectedCompany ? <p className="empty-state">등록된 기업 정보가 없습니다.</p> : <>

      <section className="brief-hero">
        <div className="brief-hero-donut">
          <div className="brief-donut-wrap">
            <Donut total={periodStoryCount} segments={[
              { key: "risk", value: periodRiskCount, color: "var(--dv-accent)" },
              { key: "negative", value: negativeExcludingRisk, color: "var(--dv-accent-2)" },
              { key: "normal", value: normalCount, color: "var(--sig-border-strong)" },
            ]} />
            <div className="brief-donut-label"><strong>{formatNumber(periodStoryCount)}</strong><span>분석 완료 이슈</span></div>
          </div>
          <div className="brief-donut-pill"><i aria-hidden="true" />위험 {formatNumber(periodRiskCount)} · 부정 {formatNumber(negativeExcludingRisk)}</div>
          <p className="brief-donut-legend">링의 진한 보라 = 위험 판정, 연한 보라 = 부정 감성(위험 아님), 회색 = 정상</p>
        </div>

        <div className="brief-hero-summary">
          <span className="brief-period-chip"><CalendarIcon />{periodLabel} · {dayCount}일</span>
          <p className="brief-headline">{headline}</p>

          <div className="brief-stat-bars">
            <div className="brief-stat-bar-row">
              <span className="label"><i style={{ background: "var(--dv-accent)" }} />위험 판정 이슈</span>
              <span className="value">{formatPercent(riskRatio)} <small>{formatNumber(periodRiskCount)}건</small></span>
              <div className="brief-stat-bar-track"><i style={{ width: `${Math.round(riskRatio * 100)}%`, background: "var(--dv-accent)" }} /></div>
            </div>
            <div className="brief-stat-bar-row">
              <span className="label"><i style={{ background: "var(--dv-accent-2)" }} />부정 감성 이슈 (위험 제외)</span>
              <span className="value">{formatPercent(negativeRatio)} <small>{formatNumber(negativeExcludingRisk)}건</small></span>
              <div className="brief-stat-bar-track"><i style={{ width: `${Math.round(negativeRatio * 100)}%`, background: "var(--dv-accent-2)" }} /></div>
            </div>
            <div className="brief-stat-bar-row">
              <span className="label"><i style={{ background: "var(--sig-border-strong)" }} />정상 이슈</span>
              <span className="value">{formatPercent(normalRatio)} <small>{formatNumber(normalCount)}건</small></span>
              <div className="brief-stat-bar-track"><i style={{ width: `${Math.round(normalRatio * 100)}%`, background: "var(--sig-border-strong)" }} /></div>
            </div>
          </div>

          <div className="brief-hero-actions">
            <button type="button" className="brief-cta" onClick={() => listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}><BellIcon />위험 사건 {formatNumber(allRiskEvents.length)}건 확인</button>
            <button type="button" className="brief-link" onClick={() => navigate("/analysis/collection")}>분석 파이프라인 열기 <ArrowUpRightIcon /></button>
          </div>
        </div>
      </section>

      <section className="brief-lower-grid">
        <article className="brief-panel" ref={listRef}>
          <div className="brief-list-head">
            <div>
              <h3>위험 사건 및 대응방안 <em>{formatNumber(allRiskEvents.length)}</em></h3>
              <p>확률이 높은 순서. 카드를 열면 근거 기사와 대응 초안을 볼 수 있습니다.</p>
            </div>
            {sortedRiskEvents.length > LIST_VISIBLE_COUNT && <button type="button" className="brief-link" onClick={() => setExpanded((current) => !current)}>{expanded ? "간략히 보기" : "전체 보기"} <ArrowUpRightIcon /></button>}
          </div>
          <div className="brief-list">
            {!riskPageData && !riskPageError ? <p className="brief-empty-note">위험 사건을 불러오는 중입니다.</p>
              : riskPageError && !riskPageData ? <p className="brief-empty-note">위험 사건을 불러오지 못했습니다.</p>
              : visibleRiskEvents.length ? visibleRiskEvents.map((risk) => {
                const band = severityBandOf(risk);
                const types = [...(risk.risk_types ?? [])].sort((left, right) => Number(right.is_primary) - Number(left.is_primary));
                const primaryType = types[0] ? RISK_TYPE_LABELS[types[0].risk_type] ?? types[0].risk_type : null;
                const dateValue = risk.last_evidence_at ?? risk.last_seen_at ?? risk.opened_at ?? risk.detected_at;
                return <button className="brief-list-row" type="button" onClick={() => onOpenRisk(selectedCompanyId, risk.id)} key={risk.id} aria-label={riskEventTitle(risk) + " 상세 보기"}>
                  <span className={`brief-list-prob ${band}`}><strong>{Math.round((risk.risk_probability ?? 0) * 100)}%</strong><span>위험</span></span>
                  <span className="brief-list-body">
                    <span className="brief-list-title-row">
                      <span className={`brief-badge ${band}`}>{SEVERITY_LABEL[band]}</span>
                      <span className="brief-list-title">{riskEventTitle(risk)}</span>
                    </span>
                    <span className="brief-list-meta">
                      {primaryType && <span>{primaryType}</span>}
                      <span>{shortDate(dateValue)}</span>
                      <span>기사 {formatNumber(risk.evidence_article_count ?? 0)}</span>
                      <span>언론사 {formatNumber(risk.source_count ?? 0)}</span>
                    </span>
                  </span>
                  <span className="brief-list-chevron"><ChevronIcon /></span>
                </button>;
              }) : <p className="brief-empty-note">선택한 기간에 위험 사건이 없습니다.</p>}
          </div>
        </article>

        <article className="brief-panel">
          <div className="brief-panel-head"><div><h3>날짜별 위험·부정 비율</h3></div></div>
          {peakDay && periodRiskCount > 0 && <p className="brief-bar-note">{shortDate(peakDay.date)}에 위험 비율 <strong>{formatPercent(peakDay.riskRatioDay)}</strong>로 정점</p>}
          {barPoints.length ? <div className="brief-bar-chart">
            <div className="brief-bar-axis">{axisTicks.map((tick) => <span key={tick}>{Math.round(tick * 100)}%</span>)}</div>
            <div className="brief-bar-columns">
              {barPoints.map((point) => <div className="brief-bar-col" key={point.date}>
                <div className="brief-bar-pair" title={`${shortDate(point.date)} · 위험 ${formatPercent(point.riskRatioDay)} · 부정 ${formatPercent(point.negRatioDay)}`}>
                  <i className="negative" style={{ height: `${Math.min(100, Math.round(point.negRatioDay / axisMax * 100))}%` }} />
                  <i className="risk" style={{ height: `${Math.min(100, Math.round(point.riskRatioDay / axisMax * 100))}%` }} />
                </div>
                <span className={`brief-bar-date${peakDay && point.date === peakDay.date && periodRiskCount > 0 ? " peak" : ""}`}>{shortDate(point.date)}</span>
              </div>)}
            </div>
          </div> : <p className="brief-empty-note">표시할 날짜별 데이터가 없습니다.</p>}
          <div className="brief-bar-legend"><span><i style={{ background: "var(--dv-accent-2)" }} />부정</span><span><i style={{ background: "var(--dv-accent)" }} />위험</span></div>
          <div className="brief-tip">
            <BulbIcon />
            <div><strong>위험은 부정의 부분집합입니다</strong><p>부정 감성 기사 중 사건화 기준(정제 기사 2건 이상)을 넘긴 것만 위험으로 셉니다.</p></div>
          </div>
        </article>
      </section>
    </>}
  </section>;
}
