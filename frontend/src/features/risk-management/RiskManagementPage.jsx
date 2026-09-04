import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { Pagination } from "../../shared/components";
import { RISK_TYPE_LABELS, formatNumber } from "../../shared/presentation";
import { RiskDetail, RiskEventListContent } from "../analysis/AnalysisStatisticsPage";

const HISTORY_PERIOD_OPTIONS = [
  { value: "3", label: "3일" },
  { value: "7", label: "7일" },
  { value: "all", label: "전체" },
];
const VIEW_OPTIONS = new Set(["active", "history"]);
const HISTORY_PERIODS = new Set(HISTORY_PERIOD_OPTIONS.map((option) => option.value));
const RESPONSE_OPTIONS = new Set(["all", "needs_action", "in_progress", "generated", "none"]);
const PAGE_SIZE = 10;

const positiveInteger = (value, fallback = 1) => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
};

// 기사 판정과 스토리 군집으로 확정된 위험 사건을 목록·근거·대응방안으로 관리한다.
export default function RiskManagementPage({ canReview = false, initialCompanyId = null, initialPeriodDays = 7 }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [companies, setCompanies] = useState([]);
  const [pageData, setPageData] = useState({ items: [], total: 0, summary: { active: 0, critical: 0, needs_response: 0, history: 0 } });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const loadSequence = useRef(0);

  const selectedCompanyId = searchParams.get("companyId") || (initialCompanyId ? String(initialCompanyId) : "");
  const requestedView = searchParams.get("view") ?? "history";
  const eventView = VIEW_OPTIONS.has(requestedView) ? requestedView : "history";
  const requestedPeriod = searchParams.get("days") ?? String(initialPeriodDays || 7);
  const period = HISTORY_PERIODS.has(requestedPeriod) ? requestedPeriod : "7";
  const page = positiveInteger(searchParams.get("page"), 1);
  const severity = ["warning", "critical"].includes(searchParams.get("severity")) ? searchParams.get("severity") : "all";
  const riskType = Object.hasOwn(RISK_TYPE_LABELS, searchParams.get("risk_type")) ? searchParams.get("risk_type") : "all";
  const requestedResponse = searchParams.get("response") ?? "all";
  const responseStatus = RESPONSE_OPTIONS.has(requestedResponse) ? requestedResponse : "all";
  const selectedRiskId = positiveInteger(searchParams.get("eventId") ?? searchParams.get("riskEventId"), 0) || null;

  const updateQuery = useCallback((changes, { resetPage = false, clearSelection = false } = {}) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(changes).forEach(([key, value]) => {
        if (value == null || value === "") next.delete(key);
        else next.set(key, String(value));
      });
      if (resetPage) next.delete("page");
      if (clearSelection) {
        next.delete("eventId");
        next.delete("riskEventId");
      }
      return next;
    }, { replace: false });
  }, [setSearchParams]);

  useEffect(() => {
    let active = true;
    api.get("/companies")
      .then((response) => {
        if (!active) return;
        const nextCompanies = response.data ?? [];
        setCompanies(nextCompanies);
        const requested = nextCompanies.find((company) => String(company.id) === selectedCompanyId);
        const fallback = nextCompanies.find((company) => company.company_role === "main") ?? nextCompanies[0];
        if (!requested && fallback) updateQuery({ companyId: fallback.id }, { resetPage: true, clearSelection: true });
      })
      .catch((requestError) => active && setError(getErrorMessage(requestError)));
    return () => { active = false; };
  }, [selectedCompanyId, updateQuery]);

  const loadRisks = useCallback(async ({ silent = false } = {}) => {
    if (!selectedCompanyId) {
      setPageData((current) => ({ ...current, items: [], total: 0 }));
      setLoading(false);
      return;
    }
    const requestId = ++loadSequence.current;
    if (!silent) setLoading(true);
    const params = new URLSearchParams({
      view: eventView,
      page: String(page),
      page_size: String(PAGE_SIZE),
      response: responseStatus,
    });
    if (eventView === "history" && period !== "all") params.set("days", period);
    if (severity !== "all") params.set("severity", severity);
    if (riskType !== "all") params.set("risk_type", riskType);
    try {
      const response = await api.get(`/companies/${selectedCompanyId}/risk-events/page?${params}`);
      if (requestId !== loadSequence.current) return;
      const data = response.data ?? {};
      const total = Number(data.total) || 0;
      const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
      if (page > lastPage) {
        updateQuery({ page: lastPage }, { clearSelection: true });
        return;
      }
      setPageData({
        items: data.items ?? [],
        total,
        summary: data.summary ?? { active: 0, critical: 0, needs_response: 0, history: 0 },
      });
      setError(null);
    } catch (requestError) {
      if (requestId === loadSequence.current) setError(getErrorMessage(requestError));
    } finally {
      if (requestId === loadSequence.current) setLoading(false);
    }
  }, [eventView, page, period, responseStatus, riskType, selectedCompanyId, severity, updateQuery]);

  useEffect(() => {
    loadRisks();
    const timer = window.setInterval(() => loadRisks({ silent: true }), 30000);
    return () => {
      window.clearInterval(timer);
      loadSequence.current += 1;
    };
  }, [loadRisks]);

  const selectedRisk = useMemo(
    () => pageData.items.find((risk) => risk.id === selectedRiskId) ?? pageData.items[0] ?? null,
    [pageData.items, selectedRiskId],
  );

  useEffect(() => {
    if (loading) return;
    if (!selectedRisk) {
      if (selectedRiskId) updateQuery({ eventId: null, riskEventId: null });
      return;
    }
    if (selectedRisk.id !== selectedRiskId) {
      updateQuery({ eventId: selectedRisk.id, riskEventId: null });
    }
  }, [loading, selectedRisk, selectedRiskId, updateQuery]);

  useEffect(() => {
    if (!selectedRisk || !["pending", "generating"].includes(selectedRisk.response_generation_status)) return undefined;
    const timer = window.setInterval(() => loadRisks({ silent: true }), 5000);
    return () => window.clearInterval(timer);
  }, [loadRisks, selectedRisk]);

  const mainCompanies = companies.filter((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");
  const historyTitle = period === "all" ? "종료 사건 · 전체" : `종료 사건 · 최근 ${period}일`;

  return <section className="workspace analysis-statistics-workspace risk-management-workspace">
    <div className="risk-summary-grid" aria-label="위험 사건 요약">
      <article><span>활성 사건</span><strong>{formatNumber(pageData.summary.active)}</strong><small>지금 추적 중</small></article>
      <article className="needs-response"><span>대응 필요</span><strong>{formatNumber(pageData.summary.needs_response)}</strong><small>보류 또는 실패</small></article>
      <article><span>종료 이력</span><strong>{formatNumber(pageData.summary.history)}</strong><small>전체 종료 사건</small></article>
    </div>

    <div className="monitor-toolbar risk-management-toolbar">
      <div className="analysis-toolbar-filters">
        <label><span className="analysis-field-label">관리 기업</span><select value={selectedCompanyId} onChange={(event) => updateQuery({ companyId: event.target.value }, { resetPage: true, clearSelection: true })}><option value="" disabled>기업을 선택하세요</option>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="비교 기업">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></label>
        <label><span className="analysis-field-label">위험 유형</span><select value={riskType} onChange={(event) => updateQuery({ risk_type: event.target.value }, { resetPage: true, clearSelection: true })}><option value="all">전체 유형</option>{Object.entries(RISK_TYPE_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label><span className="analysis-field-label">심각도</span><select value={severity} onChange={(event) => updateQuery({ severity: event.target.value }, { resetPage: true, clearSelection: true })}><option value="all">전체 심각도</option><option value="critical">긴급</option><option value="warning">주의</option></select></label>
        <label><span className="analysis-field-label">대응 상태</span><select value={responseStatus} onChange={(event) => updateQuery({ response: event.target.value }, { resetPage: true, clearSelection: true })}><option value="all">전체 상태</option><option value="needs_action">대응 필요</option><option value="in_progress">생성 중</option><option value="generated">생성 완료</option><option value="none">미생성</option></select></label>
        {eventView === "history" && <label><span className="analysis-field-label">이력 기간</span><select value={period} onChange={(event) => updateQuery({ days: event.target.value }, { resetPage: true, clearSelection: true })}>{HISTORY_PERIOD_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>}
      </div>
    </div>

    <div className="risk-view-tabs" role="tablist" aria-label="사건 목록 구분">
      <button type="button" role="tab" aria-selected={eventView === "history"} className={eventView === "history" ? "active" : ""} onClick={() => updateQuery({ view: "history", days: period }, { resetPage: true, clearSelection: true })}>종료 이력 <strong>{formatNumber(pageData.summary.history)}</strong></button>
      <button type="button" role="tab" aria-selected={eventView === "active"} className={eventView === "active" ? "active" : ""} onClick={() => updateQuery({ view: "active" }, { resetPage: true, clearSelection: true })}>활성·검토 필요 <strong>{formatNumber(pageData.summary.active)}</strong></button>
    </div>

    {error && <div className="notice error">{error}</div>}
    {!companies.length && !loading ? <p className="empty-state">먼저 기업 등록 페이지에서 관리할 기업을 등록해 주세요.</p> : <div className="risk-management-split">
      <section className="panel risk-event-list-panel">
        <div className="risk-section-heading"><div><span className="eyebrow">RISK EVENTS</span><h2>{eventView === "active" ? "활성·검토 필요 사건" : historyTitle}</h2></div><small>총 {formatNumber(pageData.total)}건</small></div>
        <div className="risk-list selectable">{loading && !pageData.items.length ? <p className="panel-empty">사건을 불러오는 중입니다.</p> : pageData.items.length ? pageData.items.map((risk) => <button className={`risk-event-list-item ${selectedRisk?.id === risk.id ? "selected" : ""}`} type="button" onClick={() => updateQuery({ eventId: risk.id, riskEventId: null })} key={risk.id}><RiskEventListContent risk={risk} /></button>) : <p className="panel-empty">{eventView === "active" ? "현재 활성 위험 사건이 없습니다." : "선택한 기간과 조건에 맞는 종료 사건이 없습니다."}</p>}</div>
        <Pagination page={page} pageSize={PAGE_SIZE} total={pageData.total} onChange={(nextPage) => updateQuery({ page: nextPage }, { clearSelection: true })} />
      </section>
      <section className="panel risk-detail-panel"><div className="risk-section-heading"><div><span className="eyebrow">EVIDENCE & RESPONSE</span><h2>위험 근거와 대응방안</h2></div></div><RiskDetail risk={selectedRisk} canReview={canReview} onGenerationStarted={() => loadRisks({ silent: true })} /></section>
    </div>}
  </section>;
}
