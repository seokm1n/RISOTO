import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { PanelTitle } from "../../shared/components";
import { formatNumber } from "../../shared/presentation";
import { RiskDetail, RiskEventListContent } from "../analysis/AnalysisStatisticsPage";

const PERIOD_OPTIONS = [
  { value: "all", label: "전체" },
  { value: "1", label: "1일" },
  { value: "3", label: "3일" },
  { value: "7", label: "7일" },
];
const PERIODS = new Set(PERIOD_OPTIONS.map((option) => option.value));
const PAGE_SIZE = 100;

const positiveInteger = (value, fallback = 1) => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
};

// 기사 판정과 스토리 군집으로 확정된 위험 사건의 대응방안을 관리한다.
export default function RiskManagementPage({ canReview = false, initialCompanyId = null, initialRiskEventId = null, initialPeriodDays = "all", embedded = false }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [companies, setCompanies] = useState([]);
  const [pageData, setPageData] = useState({ items: [], total: 0, summary: { active: 0, critical: 0, needs_response: 0, history: 0 } });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [listOpen, setListOpen] = useState(false);
  const loadSequence = useRef(0);
  const dropdownRef = useRef(null);

  const selectedCompanyId = searchParams.get("companyId") || (initialCompanyId ? String(initialCompanyId) : "");
  const eventView = searchParams.get("view") === "needs_response" ? "needs_response" : "all";
  const requestedPeriod = searchParams.get("days") ?? String(initialPeriodDays || "all");
  const period = PERIODS.has(requestedPeriod) ? requestedPeriod : "all";
  const selectedRiskId = positiveInteger(
    searchParams.get("eventId") ?? searchParams.get("riskEventId") ?? initialRiskEventId,
    0,
  ) || null;

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
      view: eventView === "needs_response" ? "active" : "all",
      page: "1",
      page_size: String(PAGE_SIZE),
      response: eventView === "needs_response" ? "needs_action" : "all",
    });
    if (period !== "all") params.set("days", period);
    try {
      const response = await api.get(`/companies/${selectedCompanyId}/risk-events/page?${params}`);
      if (requestId !== loadSequence.current) return;
      const data = response.data ?? {};
      const total = Number(data.total) || 0;
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
  }, [eventView, period, selectedCompanyId]);

  useEffect(() => {
    loadRisks();
    const timer = window.setInterval(() => loadRisks({ silent: true }), 30000);
    return () => {
      window.clearInterval(timer);
      loadSequence.current += 1;
    };
  }, [loadRisks]);

  useEffect(() => {
    setListOpen(false);
  }, [eventView, period, selectedCompanyId]);

  useEffect(() => {
    if (!listOpen) return undefined;
    const closeOutside = (event) => {
      if (!dropdownRef.current?.contains(event.target)) setListOpen(false);
    };
    const closeWithEscape = (event) => {
      if (event.key === "Escape") setListOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeWithEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeWithEscape);
    };
  }, [listOpen]);

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

  const riskCount = (pageData.summary.active ?? 0) + (pageData.summary.history ?? 0);
  const listTitle = eventView === "needs_response" ? "검토 필요 사건 선택" : "위험 사건 선택";
  const emptyMessage = eventView === "needs_response"
    ? "선택한 기간에 검토가 필요한 위험 사건이 없습니다."
    : "선택한 기간에 마지막 관련 기사가 추가된 위험 사건이 없습니다.";
  const openEvidence = () => {
    if (!selectedRisk || !selectedCompanyId) return;
    const params = new URLSearchParams({
      companyId: String(selectedCompanyId),
      eventId: String(selectedRisk.id),
      classification: "risk",
      days: period,
    });
    navigate(`/analysis/risk?${params}`);
  };

  return <section className={`${embedded ? "risk-management-embedded" : "workspace"} analysis-statistics-workspace risk-management-workspace pipeline-stage-content`}>
    <div className="pipeline-stat-grid risk-stage-stat-grid" aria-label="대응 위험 사건 분류">
      <button className={`pipeline-stat danger selectable${eventView === "all" ? " active" : ""}`} type="button" aria-pressed={eventView === "all"} onClick={() => updateQuery({ view: "all", response: null }, { resetPage: true, clearSelection: true })}><span>위험</span><strong>{formatNumber(riskCount)}건</strong><small>활성·종료 통합</small></button>
      <button className={`pipeline-stat warning selectable${eventView === "needs_response" ? " active" : ""}`} type="button" aria-pressed={eventView === "needs_response"} onClick={() => updateQuery({ view: "needs_response", response: null }, { resetPage: true, clearSelection: true })}><span>검토 필요</span><strong>{formatNumber(pageData.summary.needs_response)}건</strong><small>미생성·보류·실패</small></button>
    </div>

    {error && <div className="notice error">{error}</div>}
    {!companies.length && !loading ? <p className="empty-state">먼저 기업 등록 페이지에서 관리할 기업을 등록해 주세요.</p> : <>
      <section className="panel pipeline-panel pipeline-risk-picker-panel">
        <div className="pipeline-panel-heading pipeline-risk-list-heading"><PanelTitle title={listTitle} /><div className="pipeline-risk-list-controls"><select aria-label="조회 기간" value={period} onChange={(event) => updateQuery({ days: event.target.value }, { resetPage: true, clearSelection: true })}>{PERIOD_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></div></div>
        {selectedRisk ? <div className={`pipeline-risk-dropdown${listOpen ? " open" : ""}`} ref={dropdownRef}>
          <button className="pipeline-risk-dropdown-trigger risk-event-list-item selected" type="button" aria-expanded={listOpen} aria-controls="response-risk-event-list" onClick={() => setListOpen((open) => !open)}>
            <div className="pipeline-risk-dropdown-value"><RiskEventListContent risk={selectedRisk} judgmentCompact /></div>
            <span className="pipeline-risk-dropdown-action">{listOpen ? "목록 접기" : "목록 펼치기"}<i aria-hidden="true" /></span>
          </button>
          {listOpen && <div className="pipeline-risk-dropdown-menu risk-list selectable" id="response-risk-event-list" aria-label={eventView === "needs_response" ? "검토 필요 사건 목록" : "위험 사건 목록"}>{pageData.items.map((risk) => <button className={`risk-event-list-item ${selectedRisk.id === risk.id ? "selected" : ""}`} type="button" aria-pressed={selectedRisk.id === risk.id} onClick={() => { setListOpen(false); updateQuery({ eventId: risk.id, riskEventId: null }); }} key={risk.id}><RiskEventListContent risk={risk} judgmentCompact /></button>)}</div>}
        </div> : <p className="panel-empty">{loading ? "사건을 불러오는 중입니다." : emptyMessage}</p>}
      </section>
      <section className="panel pipeline-panel pipeline-risk-evidence">
        <div className="pipeline-panel-heading pipeline-risk-detail-heading">
          <PanelTitle kicker="RESPONSE PLAN" title="대응 방안" />
          {selectedRisk && <button className="secondary-button" type="button" onClick={openEvidence}>근거 보기</button>}
        </div>
        <RiskDetail risk={selectedRisk} canReview={canReview} onGenerationStarted={() => loadRisks({ silent: true })} />
      </section>
    </>}
  </section>;
}
