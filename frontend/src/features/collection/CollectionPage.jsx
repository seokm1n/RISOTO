import { useCallback, useEffect, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { IncidentList, PanelTitle } from "../../shared/components";
import {
  HEALTH_STATUS_LABELS,
  MONITORING_LABELS,
  SOURCE_LABELS,
  formatDate,
  formatNumber,
} from "../../shared/presentation";

// 전체 수집기 상태와 사용자별 기업의 실시간 수집 현황 및 제어 기능을 제공한다.
export default function CollectionPage({ onOpenCompany }) {
  const [companies, setCompanies] = useState([]);
  const [summaries, setSummaries] = useState({});
  const [health, setHealth] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [companyResponse, healthResponse, incidentResponse] = await Promise.all([api.get("/companies"), api.get("/collection-health"), api.get("/collection-incidents?page=1&page_size=10")]);
      const nextCompanies = companyResponse.data;
      const summaryResults = await Promise.allSettled(nextCompanies.map((company) => api.get(`/companies/${company.id}/monitoring`)));
      setCompanies(nextCompanies); setHealth(healthResponse.data); setIncidents(incidentResponse.data.items);
      setSummaries(Object.fromEntries(summaryResults.flatMap((result, index) => result.status === "fulfilled" ? [[nextCompanies[index].id, result.value.data]] : [])));
      setError(null);
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); const timer = window.setInterval(load, 30000); return () => window.clearInterval(timer); }, [load]);

  const changeAll = async (action) => {
    if (!window.confirm(`모든 기업의 실시간 수집을 ${action === "pause" ? "중지" : "재개"}할까요?`)) return;
    setBusy(`all-${action}`);
    try { await api.post(`/companies/monitoring/bulk/${action}`); await load(); }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBusy(null); }
  };
  const changeCompany = async (company) => {
    const action = company.monitoring_status === "paused" ? "resume" : "pause";
    setBusy(company.id);
    try { await api.post(`/companies/${company.id}/monitoring/${action}`); await load(); }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBusy(null); }
  };
  const acknowledgeIncident = async (incidentId) => {
    try { await api.post(`/collection-incidents/${incidentId}/acknowledge`); await load(); }
    catch (requestError) { setError(getErrorMessage(requestError)); }
  };
  const activeCount = companies.filter((company) => company.monitoring_status === "active").length;

  return <section className="workspace collection-workspace">
    <div className="workspace-head"><div><span className="eyebrow">LIVE COLLECTION / 02</span><h1>수집</h1><p>수집기 상태와 기업별 실시간 데이터 유입을 30초마다 갱신합니다.</p></div><span className="live-indicator"><i /> LIVE</span></div>
    {error && <div className="notice error">{error}</div>}
    <div className="collection-summary-grid">
      <section className="panel collection-health-card"><PanelTitle kicker="COLLECTOR HEALTH" title="수집 시스템" />{health ? <><div className={`health-state ${health.status}`}><strong>{health.status === "healthy" ? "정상" : health.status === "degraded" ? "일부 장애" : health.status === "unavailable" ? "수집 불가" : "확인 전"}</strong><span>열린 장애 {formatNumber(health.open_incident_count)}건</span></div><div className="source-health-list">{health.sources.map((source) => <div key={source.source}><span>{SOURCE_LABELS[source.source] ?? source.source}</span><strong className={source.status}>{HEALTH_STATUS_LABELS[source.status] ?? source.status}</strong><small>연속 실패 {source.consecutive_failures}회</small></div>)}</div></> : <p className="panel-empty">수집기 상태를 불러오는 중입니다.</p>}</section>
      <section className="panel collection-control-card"><PanelTitle kicker="COLLECTION CONTROL" title="전체 수집 현황" /><div className="collection-big-stat"><strong>{formatNumber(activeCount)}</strong><span>/ {formatNumber(companies.length)}개 기업 수집 활성</span></div><p>기업별 기사와 댓글을 15분 단위로 수집하고 정제 파이프라인으로 전달합니다.</p><div className="bulk-monitor-controls collection-bulk-controls"><button className="monitor-control stop" type="button" onClick={() => changeAll("pause")} disabled={Boolean(busy)}>{busy === "all-pause" ? "중지 중..." : "전체 중지"}</button><button className="monitor-control start" type="button" onClick={() => changeAll("resume")} disabled={Boolean(busy)}>{busy === "all-resume" ? "재개 중..." : "전체 재개"}</button></div></section>
    </div>
    <section className="collection-company-section"><div className="section-title home-section-title"><div><span className="eyebrow">COMPANY STREAMS</span><h2>기업별 수집 현황</h2></div></div>{loading ? <p className="empty-state">수집 현황을 불러오는 중입니다.</p> : <div className="collection-company-list">{companies.map((company) => { const summary = summaries[company.id]; const canToggle = ["active", "paused"].includes(company.monitoring_status); return <article className="collection-company-row" key={company.id}><div><span className={`status-dot ${company.monitoring_status}`} /><div><strong>{company.name}</strong><small>{company.industry_name} · {MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</small></div></div><dl><div><dt>정제 기사</dt><dd>{formatNumber(summary?.article_count)}</dd></div><div><dt>분석 완료</dt><dd>{formatNumber(summary?.analyzed_count)}</dd></div><div><dt>마지막 수집</dt><dd>{formatDate(summary?.last_collected_at)}</dd></div></dl><div className="collection-row-actions"><button type="button" onClick={() => onOpenCompany(company.id)}>상세 보기</button>{canToggle && <button className={`collection-toggle ${company.monitoring_status === "paused" ? "start" : "stop"}`} type="button" onClick={() => changeCompany(company)} disabled={Boolean(busy)}>{busy === company.id ? "처리 중..." : company.monitoring_status === "paused" ? "수집 재개" : "수집 중지"}</button>}</div></article>; })}</div>}</section>
    <section className="panel collection-incidents-panel"><PanelTitle kicker="COLLECTION INCIDENTS" title="최근 수집 장애" /><IncidentList incidents={incidents} companies={companies} onAcknowledge={acknowledgeIncident} /></section>
  </section>;
}
