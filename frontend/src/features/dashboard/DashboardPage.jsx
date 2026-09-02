import { useCallback, useEffect, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { IncidentList, Metric, PanelTitle } from "../../shared/components";
import {
  MONITORING_LABELS,
  formatDate,
  formatNumber,
  formatRiskProbability,
  isRiskDetectionAvailable,
  riskEventTitle,
  sentimentKind,
  sentimentText,
} from "../../shared/presentation";

// 선택 기간의 전체 수집량, 감성, 기업 상태와 위험 통계를 표시한다.
export default function DashboardPage() {
  const [overview, setOverview] = useState(null); const [days, setDays] = useState(7); const [error, setError] = useState(null);
  const [riskDetectionStatus, setRiskDetectionStatus] = useState(null);
  const riskDecisionReady = isRiskDetectionAvailable(riskDetectionStatus);
  // 선택 기간의 대시보드 요약을 API에서 다시 불러온다.
  const refresh = useCallback(async () => { try { const [overviewResponse, statusResponse] = await Promise.all([api.get(`/dashboard/overview?days=${days}`), api.get("/risk-detection-status")]); setOverview(overviewResponse.data); setRiskDetectionStatus(statusResponse.data); setError(null); } catch (requestError) { setRiskDetectionStatus(null); setError(getErrorMessage(requestError)); } }, [days]);
  // 대시보드 통계는 실시간 화면보다 느린 30초 간격으로 갱신한다.
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 30000); return () => window.clearInterval(timer); }, [refresh]);
  // 수집이 있었던 날짜만 그래프에 남기고 막대 높이의 기준값을 계산한다.
  const collectedDays = (overview?.daily ?? []).filter((item) => (item.article_count ?? 0) > 0);
  const maximum = Math.max(...collectedDays.map((item) => item.article_count), 1);
  const sentimentTotal = overview?.sentiments.reduce((sum, item) => sum + item.count, 0) ?? 0;
  return <section className="workspace"><div className="workspace-head"><div><span className="eyebrow">ANALYTICS / 04</span><h1>통계 대시보드</h1><p>{riskDecisionReady ? "수집량, 감성 흐름과 LightGBM 최종 위험 판정을 한눈에 확인합니다." : "수집량과 감성 흐름을 확인합니다. 최종 위험 판정은 LightGBM 운영 후 제공됩니다."}</p></div><label className="range-select">기간<select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={7}>최근 7일</option><option value={14}>최근 14일</option><option value={30}>최근 30일</option></select></label></div>
    {error && <div className="notice error">{error}</div>}
    {!overview ? <p className="empty-state">통계를 불러오는 중입니다.</p> : <><div className="metric-grid dashboard-metrics"><Metric label="모니터링 기업" value={overview.total_companies} /><Metric label="실시간 모니터링 활성" value={overview.active_companies} tone="success" /><Metric label={`${days}일 수집 기사`} value={overview.article_count} /><Metric label="최종 위험 판정" value={riskDecisionReady ? overview.risk_count : "판정 대기"} tone={riskDecisionReady && overview.risk_count ? "danger" : "pending"} small={!riskDecisionReady} /></div>
      <div className="dashboard-grid"><section className="panel chart-panel"><PanelTitle kicker="COLLECTION VOLUME" title="일별 수집량" /><div className="bar-chart">{collectedDays.length ? collectedDays.map((item) => <div className="bar-item" key={item.day}><div className="bar-value">{item.article_count}</div><div className="bar-track"><i style={{ height: `${Math.max(6, item.article_count / maximum * 100)}%` }} /></div><small>{new Date(item.day).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}</small></div>) : <p className="panel-empty">기간 내 수집 기사가 없습니다.</p>}</div></section>
        <section className="panel"><PanelTitle kicker="SENTIMENT" title="감성 분포" /><div className="sentiment-summary">{["positive", "negative", "neutral"].map((label) => { const count = overview.sentiments.find((item) => sentimentKind(item.label) === label)?.count ?? 0; return <div key={label}><span className={`sentiment-pill ${label}`}>{sentimentText(label)}</span><strong>{formatNumber(count)}건</strong><small>{sentimentTotal ? Math.round(count / sentimentTotal * 100) : 0}%</small></div>; })}</div><p className="dashboard-note">감성 분석 완료 기사 기준 · 분석 대기 기사는 포함되지 않음</p></section>
        <section className="panel span-two"><PanelTitle kicker="COMPANY STATUS" title="기업별 모니터링 현황" /><div className="company-table"><div className="table-head"><span>기업</span><span>상태</span><span>기사</span><span>부정 기사</span><span>최종 위험</span></div>{overview.companies.map((company) => { const companyRiskReady = riskDecisionReady && company.model_state === "production"; return <div className="table-row" key={company.id}><strong>{company.name}</strong><span className={`state-badge ${company.monitoring_status}`}>{MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</span><span>{formatNumber(company.article_count)}</span><span>{formatNumber(company.negative_count)}</span><span className={companyRiskReady ? "" : "decision-pending"}>{companyRiskReady ? formatNumber(company.risk_count) : "판정 대기"}</span></div>; })}</div></section>
        <section className="panel"><PanelTitle kicker="LATEST ALERTS" title="최근 위험 이벤트" /><div className="risk-list">{!riskDecisionReady ? <p className="panel-empty">LightGBM 준비 전으로 최종 위험 이벤트를 제공하지 않습니다.</p> : overview.recent_risks.length ? overview.recent_risks.map((risk) => <div className="dashboard-risk" key={risk.id}><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><strong className="risk-event-display-title">{riskEventTitle(risk)}</strong><small>위험도 {formatRiskProbability(risk.risk_probability)} · {formatDate(risk.opened_at ?? risk.detected_at)}</small></div>) : <p className="panel-empty">탐지된 위험 이벤트가 없습니다.</p>}</div></section>
        <section className="panel"><PanelTitle kicker="COLLECTION INCIDENTS" title="최근 수집 장애" /><IncidentList incidents={overview.recent_incidents} companies={overview.companies} /></section>
      </div>
    </>}
  </section>;
}
