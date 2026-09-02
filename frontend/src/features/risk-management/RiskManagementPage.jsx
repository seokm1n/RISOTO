import { useCallback, useEffect, useRef, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { PanelTitle, Pagination } from "../../shared/components";
import { RiskDetail, RiskEventListContent } from "../analysis/AnalysisStatisticsPage";

const RISK_PERIOD_OPTIONS = [
  { days: 1, label: "최근 1일" },
  { days: 3, label: "최근 3일" },
  { days: 7, label: "최근 7일" },
  { days: 14, label: "최근 14일" },
];

// 위험 이벤트의 근거와 v3 대응방안 생성·검토 흐름을 위험 관리 전용 화면에 제공한다.
export default function RiskManagementPage({ canReview = false, initialCompanyId = null, initialPeriodDays = 7 }) {
  const [companies, setCompanies] = useState([]);
  const [selectedCompanyId, setSelectedCompanyId] = useState(initialCompanyId ? String(initialCompanyId) : "");
  const [risks, setRisks] = useState([]);
  const [selectedRiskId, setSelectedRiskId] = useState(null);
  const [page, setPage] = useState(1);
  const [periodDays, setPeriodDays] = useState(RISK_PERIOD_OPTIONS.some((option) => option.days === Number(initialPeriodDays)) ? Number(initialPeriodDays) : 7);
  const [error, setError] = useState(null);
  const loadSequence = useRef(0);
  const pageSize = 10;

  const load = useCallback(async () => {
    const requestId = ++loadSequence.current;
    try {
      const companyResponse = await api.get("/companies");
      if (requestId !== loadSequence.current) return;
      const nextCompanies = companyResponse.data ?? [];
      setCompanies(nextCompanies);
      const current = nextCompanies.find((company) => String(company.id) === String(selectedCompanyId));
      const mainCompany = nextCompanies.find((company) => company.company_role === "main");
      const company = current ?? mainCompany ?? nextCompanies[0];
      if (!company) { setRisks([]); setSelectedCompanyId(""); return; }
      if (String(company.id) !== String(selectedCompanyId)) setSelectedCompanyId(String(company.id));
      const riskResponse = await api.get(`/companies/${company.id}/risk-events?limit=1000&days=${periodDays}`);
      if (requestId !== loadSequence.current) return;
      setRisks(riskResponse.data ?? []);
      setError(null);
    } catch (requestError) { if (requestId === loadSequence.current) setError(getErrorMessage(requestError)); }
  }, [selectedCompanyId, periodDays]);

  useEffect(() => { load(); const timer = window.setInterval(load, 30000); return () => { window.clearInterval(timer); loadSequence.current += 1; }; }, [load]);

  const visibleRisks = risks.slice((page - 1) * pageSize, page * pageSize);
  const selectedRisk = risks.find((risk) => risk.id === selectedRiskId) ?? visibleRisks[0] ?? null;
  const mainCompanies = companies.filter((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");

  return <section className="workspace analysis-statistics-workspace risk-management-workspace">
    <div className="workspace-head"><div><p>기업 위험 이벤트의 근거를 확인하고 v3 대응방안을 생성·검토합니다.</p></div></div>
    <div className="monitor-toolbar"><div className="analysis-toolbar-filters"><label><span className="analysis-field-label">관리 기업</span><select value={selectedCompanyId} onChange={(event) => { setSelectedCompanyId(event.target.value); setRisks([]); setPage(1); setSelectedRiskId(null); }}><option value="" disabled>기업을 선택하세요</option>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="경쟁사">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></label><label><span className="analysis-field-label">기간</span><select value={periodDays} onChange={(event) => { setPeriodDays(Number(event.target.value)); setRisks([]); setPage(1); setSelectedRiskId(null); }}>{RISK_PERIOD_OPTIONS.map((option) => <option value={option.days} key={option.days}>{option.label}</option>)}</select></label></div></div>
    {error && <div className="notice error">{error}</div>}
    {!companies.length ? <p className="empty-state">먼저 기업 등록 페이지에서 관리할 기업을 등록해 주세요.</p> : <>
      <section className="panel"><PanelTitle kicker="RISK EVENTS" title={`기업 위험 이벤트 · 최근 ${periodDays}일`} /><div className="risk-list selectable">{visibleRisks.length ? visibleRisks.map((risk) => <button className={`risk-event-list-item ${selectedRisk?.id === risk.id ? "selected" : ""}`} type="button" onClick={() => setSelectedRiskId(risk.id)} key={risk.id}><RiskEventListContent risk={risk} /></button>) : <p className="panel-empty">선택한 기간에 위험 이벤트가 없습니다.</p>}</div><Pagination page={page} pageSize={pageSize} total={risks.length} onChange={setPage} /></section>
      <section className="panel risk-detail-panel"><PanelTitle kicker="EVIDENCE & RESPONSE V3" title="위험 근거와 대응방안" /><RiskDetail risk={selectedRisk} canReview={canReview} /></section>
    </>}
  </section>;
}
