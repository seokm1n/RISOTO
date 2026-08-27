import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { api, getErrorMessage } from "../../api";
import { IncidentList, Metric, PanelTitle } from "../../shared/components";
import { OverlayLineChart } from "../realtime/RealtimePanels";
import {
  HEALTH_STATUS_LABELS,
  MODEL_STATUS_LABELS,
  MODEL_TASK_DESCRIPTIONS,
  MODEL_TASK_LABELS,
  SOURCE_LABELS,
  formatDate,
  formatNumber,
  formatPercent,
} from "../../shared/presentation";

// 모든 로그인 사용자에게 운영 모델, 학습 준비도와 품질 점검을 제공한다.
export default function ModelManagementPage() {
  const navigate = useNavigate();
  const [versions, setVersions] = useState([]);
  const [readiness, setReadiness] = useState(null);
  const [modelCheck, setModelCheck] = useState(null);
  const [riskStatus, setRiskStatus] = useState(null);
  const [llmLabeling, setLlmLabeling] = useState(null);
  const [companies, setCompanies] = useState([]);
  const [health, setHealth] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [overview, setOverview] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    try {
      const [
        versionsResponse, readinessResponse, checkResponse, statusResponse, llmResponse,
        companiesResponse, healthResponse, incidentsResponse, overviewResponse,
      ] = await Promise.all([
        api.get("/model-versions"),
        api.get("/model-training-readiness"),
        api.get("/model-monitoring"),
        api.get("/risk-detection-status"),
        api.get("/llm-labeling/status"),
        api.get("/companies"),
        api.get("/collection-health"),
        api.get("/collection-incidents?page=1&page_size=20"),
        api.get("/dashboard/overview?days=7"),
      ]);
      setVersions(versionsResponse.data);
      setReadiness(readinessResponse.data);
      setModelCheck(checkResponse.data);
      setRiskStatus(statusResponse.data);
      setLlmLabeling(llmResponse.data);
      setCompanies(companiesResponse.data);
      setHealth(healthResponse.data);
      setIncidents(incidentsResponse.data.items);
      setOverview(overviewResponse.data);
      setNotice(null);
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const acknowledgeIncident = async (incidentId) => {
    try { await api.post(`/collection-incidents/${incidentId}/acknowledge`); await load(); }
    catch (requestError) { setNotice({ type: "error", message: getErrorMessage(requestError) }); }
  };

  const promote = async (model) => {
    if (!window.confirm(`${model.version} 후보 모델을 운영 버전으로 승격할까요?`)) return;
    setBusy(`promote-${model.id}`);
    setNotice(null);
    try {
      await api.post(`/model-versions/${model.id}/promote`);
      await load();
      setNotice({ type: "success", message: `${model.version} 모델을 운영 버전으로 승격했습니다.` });
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    } finally {
      setBusy(null);
    }
  };

  const rerunCheck = async () => {
    setBusy("check");
    setNotice(null);
    try {
      const response = await api.post("/model-monitoring/check");
      setModelCheck(response.data);
      setNotice({ type: "success", message: "수집·분석 품질 점검을 다시 실행했습니다." });
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    } finally {
      setBusy(null);
    }
  };

  const runLlmLabelingBacklog = async () => {
    setBusy("llm-labeling");
    setNotice(null);
    try {
      const response = await api.post("/llm-labeling/run");
      setLlmLabeling(response.data);
      setNotice({ type: "success", message: "밀린 기사에 대한 LLM 라벨링을 실행했습니다." });
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    } finally {
      setBusy(null);
    }
  };

  const productionCount = versions.filter((model) => model.status === "production").length;
  const candidateCount = versions.filter((model) => model.status === "candidate").length;
  const readyTaskCount = readiness?.tasks?.filter((task) => task.candidate_training_ready).length ?? 0;

  return <section className="workspace model-workspace">
    <div className="workspace-head"><div><span className="eyebrow">MODEL OPERATIONS</span><h1>운영 관리</h1><p>운영 모델, 후보 승격 조건과 데이터 품질을 관리합니다.</p></div></div>
    {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
    <div className="metric-grid dashboard-metrics model-metrics"><Metric label="운영 모델" value={productionCount} /><Metric label="후보 모델" value={candidateCount} /><Metric label="학습 준비 작업" value={readyTaskCount} /><Metric label="최종 위험 판정" value={riskStatus?.risk_detection_status === "available" ? "운영 중" : "판정 대기"} tone={riskStatus?.risk_detection_status === "available" ? "" : "pending"} small /></div>
    <section className="panel analysis-status"><PanelTitle kicker="ANALYSIS RUNTIME" title="운영 분석 상태" /><div className="analysis-status-grid"><article className="active"><span>기본 기사 분석</span><strong>KLUE 기본 모델 사용 중</strong><small>관련성·광고·감성 분석과 위험 유형 분류를 보조합니다.</small></article><article className={riskStatus?.risk_detection_status === "available" ? "active" : "pending"}><span>최종 위험 판정</span><strong>{riskStatus?.risk_detection_status === "available" ? `${riskStatus.model_version ?? "LightGBM"} 운영 중` : "LightGBM 준비 전"}</strong><small>{riskStatus?.message ?? "운영 상태를 확인하고 있습니다."}</small></article></div></section>
    <div className="model-layout">
      <section className="panel model-versions-panel"><PanelTitle kicker="MODEL REGISTRY" title="모델 버전" /><div className="model-version-list">{versions.length ? versions.map((model) => <article className="model-version-row" key={model.id}><div><span className={`model-status ${model.status}`}>{MODEL_STATUS_LABELS[model.status] ?? model.status}</span><div><strong>{MODEL_TASK_LABELS[model.task] ?? model.task}</strong><small>{model.version} · {model.base_model || "사용자 정의 모델"}</small>{MODEL_TASK_DESCRIPTIONS[model.task] && <small>{MODEL_TASK_DESCRIPTIONS[model.task]}</small>}</div></div><div><small>등록 {formatDate(model.created_at)}</small>{model.status === "candidate" && <button type="button" onClick={() => promote(model)} disabled={Boolean(busy)}>{busy === `promote-${model.id}` ? "승격 중..." : "운영 승격"}</button>}</div></article>) : <p className="panel-empty">등록된 모델 버전이 없습니다.</p>}</div></section>
      <section className="panel model-readiness-panel"><PanelTitle kicker="TRAINING READINESS" title="학습 준비도" /><div className="readiness-task-list">{readiness?.tasks?.map((task) => <article className={task.candidate_training_ready ? "ready" : "blocked"} key={task.task}><div><strong>{MODEL_TASK_LABELS[task.task] ?? task.task}</strong><span>{task.candidate_training_ready ? "후보 학습 가능" : "데이터 준비 중"}</span></div><p>확정 라벨 {formatNumber(task.confirmed_total)}건 · 신규 {formatNumber(task.new_since_latest)}/{formatNumber(task.increment_required)}건</p>{task.blockers?.length > 0 && <small>{task.blockers[0]}</small>}</article>) ?? <p className="panel-empty">학습 준비도를 확인하고 있습니다.</p>}</div></section>
    </div>
    <section className="panel quality-operations model-quality"><div className="model-quality-head"><PanelTitle kicker="DAILY QUALITY CHECK" title="수집·분석 품질 점검" /><button className="secondary-button" type="button" onClick={rerunCheck} disabled={Boolean(busy)}>{busy === "check" ? "점검 중..." : "지금 다시 점검"}</button></div>{modelCheck ? <div className="daily-check-summary"><div><span>점검 상태</span><strong className={modelCheck.status}>{modelCheck.status === "stable" ? "안정" : modelCheck.status === "warning" ? "확인 필요" : "비교 자료 부족"}</strong></div><div><span>최근 특징 구간</span><strong>{formatNumber(modelCheck.report?.recent_window_count)}</strong></div><div><span>수집 커버리지</span><strong>{formatPercent(modelCheck.report?.collection_coverage)}</strong></div><div><span>분포 변화 경고</span><strong>{formatNumber(modelCheck.report?.drift_flags?.length)}</strong></div><small>마지막 점검 {formatDate(modelCheck.checked_at)}</small></div> : <p className="panel-empty">품질 점검 결과를 불러오는 중입니다.</p>}</section>
    <section className="panel quality-operations model-quality">
      <div className="model-quality-head">
        <PanelTitle kicker="LLM AUTO LABELING" title="LLM 자동 라벨링" />
        <div style={{ display: "flex", gap: 10 }}>
          <button className="secondary-button" type="button" onClick={() => navigate("/reviews?mode=audit")}>표본 검수하러 가기</button>
          <button className="secondary-button" type="button" onClick={runLlmLabelingBacklog} disabled={Boolean(busy) || !llmLabeling?.enabled}>{busy === "llm-labeling" ? "실행 중..." : "밀린 기사 지금 처리"}</button>
        </div>
      </div>
      {llmLabeling ? <>
        <p className="dashboard-note">사람이 기사를 매 건 검수하는 대신, 별도의 LLM이 독립적으로 관련성·광고·감성을 판단해 정답 라벨로 바로 저장합니다. 수집될 때마다 자동으로 실행되며, "밀린 기사 지금 처리"는 놓친 기사를 수동으로 따라잡을 때만 씁니다.</p>
        <div className="daily-check-summary">
          <div><span>가동 상태</span><strong className={llmLabeling.enabled ? "stable" : "warning"}>{llmLabeling.enabled ? "자동 실행 중" : llmLabeling.provider === "ollama" ? "Ollama 연결 필요" : "API 키 필요"}</strong></div>
          <div><span>사용 모델</span><strong>{llmLabeling.provider === "ollama" ? "Ollama" : "OpenAI"} · {llmLabeling.model_name}</strong></div>
          <div><span>누적 라벨링</span><strong>{formatNumber(llmLabeling.llm_labeled_total)}건</strong></div>
          <div><span>최근 24시간</span><strong>{formatNumber(llmLabeling.llm_labeled_last_24h)}건</strong></div>
          <div><span>처리 대기(밀린 기사)</span><strong>{formatNumber(llmLabeling.pending_backlog)}건</strong></div>
          <div><span>{llmLabeling.audit.month} 표본 검수</span><strong>{formatNumber(llmLabeling.audit.reviewed_count)}/{formatNumber(llmLabeling.audit.target_sample_size)}건</strong></div>
          <div><span>사람·LLM 일치율</span><strong>{formatPercent(llmLabeling.audit.agreement_rate)}</strong></div>
          <small>매달 무작위 표본을 사람이 블라인드로 다시 라벨링해 LLM 판단이 흔들리지 않는지 확인합니다.</small>
        </div>
      </> : <p className="panel-empty">LLM 라벨링 현황을 불러오는 중입니다.</p>}
    </section>
    <div className="operations-grid">
      <section className="panel"><PanelTitle kicker="COLLECTION HEALTH" title="수집 시스템 상태" />{health ? <><div className={`health-state ${health.status}`}><strong>{health.status === "healthy" ? "정상" : health.status === "degraded" ? "일부 장애" : health.status === "unavailable" ? "수집 불가" : "확인 전"}</strong><span>열린 장애 {formatNumber(health.open_incident_count)}건</span></div><div className="source-health-list">{health.sources.map((source) => <div key={source.source}><span>{SOURCE_LABELS[source.source] ?? source.source}</span><strong className={source.status}>{HEALTH_STATUS_LABELS[source.status] ?? source.status}</strong><small>연속 실패 {source.consecutive_failures}회</small></div>)}</div></> : <p className="panel-empty">수집 시스템 상태를 불러오는 중입니다.</p>}</section>
      <section className="panel"><PanelTitle kicker="COLLECTION INCIDENTS" title="수집 장애" /><IncidentList incidents={incidents} companies={companies} onAcknowledge={acknowledgeIncident} /></section>
    </div>
    <section className="panel realtime-trend-panel"><PanelTitle kicker="TOTAL TREND / 7 DAYS" title={riskStatus?.risk_detection_status === "available" ? "전체 수집량 · 위험량" : "전체 수집량 · 위험 판정 대기"} />{overview ? <OverlayLineChart overview={overview} riskAvailable={riskStatus?.risk_detection_status === "available"} /> : <p className="panel-empty">전체 추세를 불러오는 중입니다.</p>}</section>
  </section>;
}
