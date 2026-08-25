import { useCallback, useEffect, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { Metric } from "../../shared/components";
import { CompanyCard } from "../companies/CompanyPages";

// 로그인 직후 등록 기업과 핵심 운영 지표를 보여 주는 공통 메인 화면이다.
export default function MainPage({ canManageCompanies = true, onOpenCompany, onManageCompanies }) {
  const [companies, setCompanies] = useState([]);
  const [overview, setOverview] = useState(null);
  const [monitoringSummaries, setMonitoringSummaries] = useState({});
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [companyResponse, overviewResponse] = await Promise.all([api.get("/companies"), api.get("/dashboard/overview?days=7")]);
      setCompanies(companyResponse.data); setOverview(overviewResponse.data);
      const results = await Promise.allSettled(companyResponse.data.map((company) => api.get(`/companies/${company.id}/monitoring`)));
      setMonitoringSummaries(Object.fromEntries(results.flatMap((result, index) => result.status === "fulfilled" ? [[companyResponse.data[index].id, result.value.data]] : [])));
      setError(null);
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); const timer = window.setInterval(load, 30000); return () => window.clearInterval(timer); }, [load]);

  const mainCompany = companies.find((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");

  return <section className="workspace home-workspace">
    <div className="workspace-head home-head"><div className="home-brand-heading"><img src="/risoto-app-icon.png" alt="" aria-hidden="true" /><div><div className="home-brand-wordmark"><h1>RISOTO</h1><span>RISk Out Through Observation</span></div><p>메인 기업과 경쟁사의 수집 상태 및 최근 위험 신호를 확인합니다.</p></div></div>{canManageCompanies && <button className="primary-action" type="button" onClick={() => onManageCompanies(null, "register")}><span>경쟁사 등록</span><b aria-hidden="true">＋</b></button>}</div>
    {error && <div className="notice error">{error}</div>}
    <div className="metric-grid dashboard-metrics home-metrics"><Metric label="등록 기업" value={overview?.total_companies ?? companies.length} /><Metric label="수집 중" value={overview?.active_companies ?? 0} tone="success" /><Metric label="최근 7일 기사" value={overview?.article_count ?? 0} /><Metric label="열린 위험" value={overview?.risk_count ?? 0} tone={overview?.risk_count ? "danger" : ""} /></div>
    {loading ? <p className="empty-state">기업 정보를 불러오는 중입니다.</p> : <>
      <div className="section-title home-section-title"><div><span className="eyebrow">MAIN COMPANY</span><h2>메인 기업</h2></div><strong>01</strong></div>
      {mainCompany ? <div className="company-list main-company-list"><CompanyCard company={mainCompany} monitoringSummary={monitoringSummaries[mainCompany.id]} onOpen={onOpenCompany} onEdit={canManageCompanies ? (companyId) => onManageCompanies(companyId, "edit") : undefined} /></div> : <p className="empty-state">메인 기업 정보가 없습니다.</p>}
      <div className="section-title home-section-title competitor-section-title"><div><span className="eyebrow">COMPETITORS</span><h2>경쟁사</h2></div><strong>{competitorCompanies.length.toString().padStart(2, "0")}</strong></div>
      {competitorCompanies.length ? <div className="company-list">{competitorCompanies.map((company) => <CompanyCard company={company} key={company.id} monitoringSummary={monitoringSummaries[company.id]} onOpen={onOpenCompany} onEdit={canManageCompanies ? (companyId) => onManageCompanies(companyId, "edit") : undefined} />)}</div> : <div className="empty-state home-empty"><p>아직 등록한 경쟁사가 없습니다.</p>{canManageCompanies && <button type="button" onClick={() => onManageCompanies(null, "register")}>경쟁사 등록하기</button>}</div>}
    </>}
  </section>;
}
