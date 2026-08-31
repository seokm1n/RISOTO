import { useCallback, useEffect, useRef, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { PanelTitle } from "../../shared/components";
import {
  RISK_TYPE_LABELS,
  formatDate,
  formatNumber,
  formatPercent,
  isRiskDetectionAvailable,
  riskEventTitle,
} from "../../shared/presentation";

const emptyRiskLabel = () => ({ is_risk: "", risk_types: [], notes: "" });

function ManagementNotifications({ data, error, panelRef, onRiskOpen }) {
  const riskItems = (data?.items ?? []).filter((item) => item.type === "risk");
  const riskCount = Number.isFinite(data?.risk_count) ? data.risk_count : riskItems.length;
  const renderItem = (item) => {
    const canOpen = Boolean(item.company_id);
    return <button className="management-notification" type="button" disabled={!canOpen} onClick={() => onRiskOpen(item)} key={`${item.type}-${item.id}`}>
      <span className="notification-kind-mark" aria-hidden="true" />
      <div><strong>{item.title}</strong><strong className="notification-article-title risk-event-display-title">{item.message}</strong><small>{formatDate(item.created_at)}</small></div>
      <span className="notification-action">{canOpen ? "실시간 사건 보기" : "연결 정보 없음"}<b aria-hidden="true">→</b></span>
    </button>;
  };
  return <section className="panel management-notifications" id="management-notifications" ref={panelRef} tabIndex={-1} aria-labelledby="management-notifications-title">
    <div className="management-notifications-head"><div><span className="eyebrow">ACTION REQUIRED</span><h2 id="management-notifications-title">위험 알림</h2><p>확인이 필요한 최종 위험 사건을 확인합니다.</p></div><div className="notification-summary"><span>위험 <strong>{formatNumber(riskCount)}</strong></span></div></div>
    {error && <div className="notification-load-error" role="status">알림을 갱신하지 못했습니다. 마지막으로 불러온 결과를 표시합니다.</div>}
    {!riskItems.length ? <p className="management-notifications-empty">현재 확인할 위험 알림이 없습니다.</p> : <div className="management-notification-list">{riskItems.map(renderItem)}</div>}
  </section>;
}

// 위험 사건 확인과 수집·분석 품질 상태를 한곳에서 관리한다.
function AnalysisManagementPage({ notifications, notificationError, focusRequest, onRiskNotificationOpen }) {
  const [risks, setRisks] = useState([]); const [modelCheck, setModelCheck] = useState(null);
  const [riskDetectionStatus, setRiskDetectionStatus] = useState(null);
  const riskDecisionReady = isRiskDetectionAvailable(riskDetectionStatus);
  const [riskLabel, setRiskLabel] = useState(emptyRiskLabel);
  const [notice, setNotice] = useState(null); const [saving, setSaving] = useState(false);
  const currentRisk = risks[0] ?? null;
  const notificationPanelRef = useRef(null);
  useEffect(() => {
    if (!focusRequest || focusRequest.target !== "notifications") return undefined;
    const frame = window.requestAnimationFrame(() => {
      notificationPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      notificationPanelRef.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusRequest]);
  const load = useCallback(async () => {
    try { const [riskResponse, statusResponse, monitoringResponse] = await Promise.all([api.get("/reviews/risk-events?limit=50"), api.get("/risk-detection-status"), api.get("/model-monitoring")]); setRisks(riskResponse.data); setRiskDetectionStatus(statusResponse.data); setModelCheck(monitoringResponse.data); setNotice(null); }
    catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const saveRisk = async (event) => {
    event.preventDefault();
    if (!currentRisk || !riskLabel.is_risk || (riskLabel.is_risk === "risk" && !riskLabel.risk_types.length)) return;
    setSaving(true);
    try { await api.post(`/reviews/risk-events/${currentRisk.id}`, { is_risk: riskLabel.is_risk === "risk", event_start: currentRisk.opened_at ?? currentRisk.detected_at, event_end: currentRisk.closed_at, risk_types: riskLabel.is_risk === "risk" ? riskLabel.risk_types : [], evidence_article_ids: currentRisk.evidence_articles.map((article) => article.article_id), status: "confirmed", notes: riskLabel.notes }); const response = await api.get("/reviews/risk-events?limit=50"); setRisks(response.data); setRiskLabel(emptyRiskLabel()); setNotice({ type: "success", message: "위험 사건 판정을 확정했습니다." }); }
    catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); } finally { setSaving(false); }
  };
  const toggleRiskType = (type) => setRiskLabel((current) => ({ ...current, risk_types: current.risk_types.includes(type) ? current.risk_types.filter((item) => item !== type) : [...current.risk_types, type] }));
  const riskSelectionComplete = Boolean(riskLabel.is_risk && (riskLabel.is_risk === "normal" || riskLabel.risk_types.length));
  return <section className="workspace review-workspace"><div className="workspace-head"><div><span className="eyebrow">MANAGEMENT / 05</span><h1>운영 관리</h1><p>기본 KLUE 모델로 기사를 분석하고, 최종 위험 사건과 수집·분석 품질을 관리합니다.</p></div></div>
    {notice && <div className={`notice ${notice.type}`}>{notice.message}</div>}
    <ManagementNotifications data={notifications} error={notificationError} panelRef={notificationPanelRef} onRiskOpen={onRiskNotificationOpen} />
    <section className="panel analysis-status"><PanelTitle kicker="ANALYSIS STATUS" title="분석 상태" /><div className="analysis-status-grid"><article className="active"><span>기사 분석</span><strong>KLUE 기본 모델 사용 중</strong><small>관련성·광고·감성 분석과 위험 유형 분류를 보조합니다.</small></article><article className={riskDecisionReady ? "active" : "pending"}><span>최종 위험 판정</span><strong>{riskDecisionReady ? "LightGBM 운영 중" : "LightGBM 준비 전"}</strong><small>{riskDetectionStatus?.message ?? "최종 위험 판정은 제공되지 않으며 위험 0으로 해석하지 않습니다."}</small></article></div></section>
    <div className="management-section-intro"><span className="eyebrow">RISK EVENT MANAGEMENT</span><h2>위험 사건 관리</h2><p>운영 중인 LightGBM이 탐지한 사건의 위험 여부와 유형을 확인합니다.</p></div>
    <div className="review-progress"><div><span>현재 사건 작업 목록</span><strong>{formatNumber(risks.length)}</strong></div></div>
    <div className="review-grid"><section className="panel"><PanelTitle kicker="RISK EVENT REVIEW" title="위험 사건 확인" />{currentRisk ? <form className="label-form" onSubmit={saveRisk}><div className="review-article"><span>{formatDate(currentRisk.opened_at ?? currentRisk.detected_at)}</span><h3><strong className="risk-event-display-title">{riskEventTitle(currentRisk)}</strong></h3>{currentRisk.evidence_articles.map((article) => <a href={article.url} target="_blank" rel="noreferrer" key={article.article_id}>{article.title}</a>)}</div><label>사건 판정<select value={riskLabel.is_risk} onChange={(event) => { const value = event.target.value; setRiskLabel((current) => ({ ...current, is_risk: value, risk_types: value === "risk" ? current.risk_types : [] })); }} required><option value="">선택하세요</option><option value="risk">실제 위험</option><option value="normal">정상·오경보</option></select></label><fieldset className="label-wide" disabled={riskLabel.is_risk !== "risk"}><legend>위험 유형 (복수 선택)</legend><div className="risk-type-checks">{Object.entries(RISK_TYPE_LABELS).map(([type, label]) => <label key={type}><input type="checkbox" checked={riskLabel.risk_types.includes(type)} onChange={() => toggleRiskType(type)} />{label}</label>)}</div></fieldset><label className="label-wide">메모<textarea value={riskLabel.notes} onChange={(event) => setRiskLabel((current) => ({ ...current, notes: event.target.value }))} /></label><button className="submit-button" disabled={saving || !riskSelectionComplete}>확정하고 다음 사건</button></form> : <p className="panel-empty">{riskDecisionReady ? "현재 관리할 신규 위험 사건이 없습니다." : "LightGBM 준비 전으로 확인할 최종 위험 사건이 없습니다."}</p>}</section>
    </div>
    <section className="panel quality-operations"><PanelTitle kicker="OPERATIONS QUALITY" title="수집·분석 품질 점검" />
      <p className="dashboard-note">기본 모델을 사용하는 동안 수집 커버리지와 데이터 분포 변화를 자동으로 점검합니다.</p>
      {modelCheck && <div className="daily-check-summary"><div><span>점검 상태</span><strong className={modelCheck.status}>{modelCheck.status === "stable" ? "안정" : modelCheck.status === "warning" ? "확인 필요" : "비교 자료 부족"}</strong></div><div><span>최근 특징 구간</span><strong>{formatNumber(modelCheck.report?.recent_window_count)}</strong></div><div><span>수집 커버리지</span><strong>{formatPercent(modelCheck.report?.collection_coverage)}</strong></div><div><span>분포 변화 경고</span><strong>{formatNumber(modelCheck.report?.drift_flags?.length)}</strong></div><small>마지막 점검 {formatDate(modelCheck.checked_at)}</small></div>}
    </section>
  </section>;
}

export default AnalysisManagementPage;
