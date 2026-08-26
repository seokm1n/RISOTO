import { useCallback, useEffect, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { PanelTitle } from "../../shared/components";
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
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [companyResponse, healthResponse] = await Promise.all([api.get("/companies"), api.get("/collection-health")]);
      const nextCompanies = companyResponse.data;
      const summaryResults = await Promise.allSettled(nextCompanies.map((company) => api.get(`/companies/${company.id}/monitoring`)));
      setCompanies(nextCompanies); setHealth(healthResponse.data);
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
  const runningCount = companies.filter((company) => ["backfilling", "warming", "active"].includes(company.monitoring_status)).length;
  const collectionRunning = runningCount > 0;
  const companyGroups = [
    { role: "main", title: "메인 기업", companies: companies.filter((company) => company.company_role === "main") },
    { role: "competitor", title: "경쟁사", companies: companies.filter((company) => company.company_role === "competitor") },
  ];
  const renderCompanyRow = (company) => {
    const summary = summaries[company.id];
    const canToggle = ["backfilling", "warming", "active", "paused"].includes(company.monitoring_status);
    return <article className="collection-company-row" key={company.id}>
      <div><span className={`status-dot ${company.monitoring_status}`} /><div><strong>{company.name}</strong><small>{company.industry_name} · {MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</small></div></div>
      <dl><div><dt>정제 기사</dt><dd>{formatNumber(summary?.article_count)}</dd></div><div><dt>분석 완료</dt><dd>{formatNumber(summary?.analyzed_count)}</dd></div><div><dt>마지막 수집</dt><dd>{formatDate(summary?.last_collected_at)}</dd></div></dl>
      <div className="collection-row-actions"><button type="button" onClick={() => onOpenCompany(company.id)}>분석 통계 보기</button>{canToggle && <button className={`collection-toggle ${company.monitoring_status === "paused" ? "start" : "stop"}`} type="button" onClick={() => changeCompany(company)} disabled={Boolean(busy)}>{busy === company.id ? "처리 중..." : company.monitoring_status === "paused" ? "수집 재개" : "수집 중지"}</button>}</div>
    </article>;
  };

  return <section className="workspace collection-workspace">
    <div className="workspace-head"><div><span className="eyebrow">LIVE COLLECTION / 02</span><h1>수집</h1><p>수집기 상태와 기업별 실시간 데이터 유입을 30초마다 갱신합니다.</p></div></div>
    {error && <div className="notice error">{error}</div>}
    <div className="collection-summary-grid">
      <section className="panel collection-health-card"><PanelTitle kicker="COLLECTOR HEALTH" title="수집 시스템" />{health ? <><div className={`health-state ${health.status}`}><strong>{health.status === "healthy" ? "정상" : health.status === "degraded" ? "일부 장애" : health.status === "unavailable" ? "수집 불가" : "확인 전"}</strong><span>열린 장애 {formatNumber(health.open_incident_count)}건</span></div><div className="source-health-list">{health.sources.map((source) => <div key={source.source}><span>{SOURCE_LABELS[source.source] ?? source.source}</span><strong className={source.status}>{HEALTH_STATUS_LABELS[source.status] ?? source.status}</strong><small>연속 실패 {source.consecutive_failures}회</small></div>)}</div></> : <p className="panel-empty">수집기 상태를 불러오는 중입니다.</p>}</section>
      <section className="panel collection-control-card">
        <PanelTitle kicker="COLLECTION CONTROL" title="전체 수집 현황" />
        <div className="collection-status-row">
          <div className="collection-big-stat"><strong>{formatNumber(runningCount)}</strong><span>/ {formatNumber(companies.length)}개 기업 수집 활성</span></div>
          <div className={`collection-activity ${collectionRunning ? "running" : "stopped"}`} role="status" aria-live="polite">
            <div><span className="collection-activity-light" aria-hidden="true" /><strong>{collectionRunning ? "수집 진행 중" : "수집 중지됨"}</strong><small>{collectionRunning ? `${formatNumber(runningCount)}개 기업의 데이터를 수집하고 있습니다.` : "현재 실행 중인 기업 수집이 없습니다."}</small></div>
            <span className="collection-activity-track" aria-hidden="true"><i /></span>
          </div>
        </div>
        <p>기업별 기사와 댓글을 15분 단위로 수집하고 정제 파이프라인으로 전달합니다.</p>
        <div className="bulk-monitor-controls collection-bulk-controls"><button className="monitor-control stop" type="button" onClick={() => changeAll("pause")} disabled={Boolean(busy)}>{busy === "all-pause" ? "중지 중..." : "전체 중지"}</button><button className="monitor-control start" type="button" onClick={() => changeAll("resume")} disabled={Boolean(busy)}>{busy === "all-resume" ? "재개 중..." : "전체 재개"}</button></div>
      </section>
    </div>
    <section className="collection-company-section"><div className="section-title home-section-title"><div><span className="eyebrow">COMPANY STREAMS</span><h2>기업별 수집 현황</h2></div></div>{loading ? <p className="empty-state">수집 현황을 불러오는 중입니다.</p> : <div className="collection-stream-groups">{companyGroups.map((group) => <section className={`collection-stream-group ${group.role}`} key={group.role}><header><h3>{group.title}</h3></header>{group.companies.length ? <div className="collection-company-list">{group.companies.map(renderCompanyRow)}</div> : <p className="collection-group-empty">등록된 {group.title}이 없습니다.</p>}</section>)}</div>}</section>
  </section>;
}
