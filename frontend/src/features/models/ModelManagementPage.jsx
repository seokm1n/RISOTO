import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { api, getErrorMessage } from "../../api";
import { IncidentList, Metric, PanelTitle } from "../../shared/components";
import {
  MODEL_STATUS_LABELS,
  MODEL_TASK_DESCRIPTIONS,
  MODEL_TASK_LABELS,
  formatDate,
  formatNumber,
  formatPercent,
} from "../../shared/presentation";

// 현재 사용 중인 운영 모델과 수집·분석 품질 상태를 확인하는 화면이다.
export default function ModelManagementPage() {
  const navigate = useNavigate();
  const [versions, setVersions] = useState([]);
  const [modelCheck, setModelCheck] = useState(null);
  const [riskStatus, setRiskStatus] = useState(null);
  const [runtimeStatus, setRuntimeStatus] = useState(null);
  const [llmLabeling, setLlmLabeling] = useState(null);
  const [companies, setCompanies] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    try {
      const [versionsResponse, checkResponse, statusResponse, runtimeResponse, llmResponse, companiesResponse, incidentsResponse] = await Promise.all([
        api.get("/model-versions"),
        api.get("/model-monitoring"),
        api.get("/risk-detection-status"),
        api.get("/model-runtime-status"),
        api.get("/llm-labeling/status"),
        api.get("/companies"),
        api.get("/collection-incidents?page=1&page_size=10"),
      ]);
      setVersions(versionsResponse.data);
      setModelCheck(checkResponse.data);
      setRiskStatus(statusResponse.data);
      setRuntimeStatus(runtimeResponse.data);
      setLlmLabeling(llmResponse.data);
      setCompanies(companiesResponse.data);
      setIncidents(incidentsResponse.data.items);
      setNotice(null);
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

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

  const acknowledgeIncident = async (incidentId) => {
    setNotice(null);
    try {
      await api.post(`/collection-incidents/${incidentId}/acknowledge`);
      await load();
      setNotice({ type: "success", message: "수집 장애를 확인 처리했습니다." });
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
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

  const runtimeModels = runtimeStatus ? [
    {
      id: "runtime-relevance", task: "topical_relevance", version: runtimeStatus.article_filter_version,
      status: runtimeStatus.relevance_model_available ? "production" : "unavailable",
      base_model: runtimeStatus.relevance_model_name, runtime: true,
      note: "별도 기업 관련성 모델로 동명이인·부수 언급을 판별합니다.",
    },
    {
      id: "runtime-advertising", task: "article_relevance", version: runtimeStatus.article_filter_version,
      status: runtimeStatus.advertising_model_available ? "production" : "unavailable",
      base_model: runtimeStatus.advertising_model_name, runtime: true,
      note: "로컬 normal/filter 모델로 광고·스팸성 기사를 판별합니다.",
    },
    {
      id: "runtime-sentiment", task: "sentiment", version: "local-klue-roberta-sentiment",
      status: runtimeStatus.sentiment_model_available ? "production" : "unavailable",
      base_model: runtimeStatus.sentiment_model_name, runtime: true,
      note: "로컬 KLUE-RoBERTa 모델로 긍정·중립·부정 감성을 분석합니다.",
    },
    {
      id: "runtime-lightgbm", task: "risk_detector", version: riskStatus?.model_version ?? runtimeStatus.external_lightgbm_model_name ?? "external-lightgbm",
      status: riskStatus?.risk_detection_status === "available" ? "production" : "unavailable",
      base_model: "LightGBM", runtime: true,
      note: riskStatus?.risk_detection_status === "available" ? riskStatus.message : runtimeStatus.external_lightgbm_message,
    },
  ] : [];
  const displayedModels = [
    ...runtimeModels.filter((model) => model.status === "production"),
    ...versions.filter((model) => model.status === "production"),
  ];
  const productionCount = displayedModels.filter((model) => model.status === "production").length;
  const monthlyReviewCount = llmLabeling?.audit
    ? `${formatNumber(llmLabeling.audit.reviewed_count)}/${formatNumber(llmLabeling.audit.target_sample_size)}건`
    : "-";

  return <section className="workspace model-workspace">
    <div className="workspace-head"><div><span className="eyebrow">MODEL OPERATIONS</span><h1>운영 관리</h1><p>현재 사용 중인 모델과 수집·분석 상태를 확인합니다.</p></div></div>
    {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
    <div className="metric-grid dashboard-metrics model-metrics"><Metric label="운영 모델" value={productionCount} /><Metric label="월간 검수량" value={monthlyReviewCount} small /><Metric label="검수 대기" value={llmLabeling?.pending_backlog ?? 0} /><Metric label="최종 위험 판정" value={riskStatus?.risk_detection_status === "available" ? "운영 중" : "판정 대기"} tone={riskStatus?.risk_detection_status === "available" ? "" : "pending"} small /></div>
    <section className="panel analysis-status">
      <div className="model-quality-head">
        <PanelTitle kicker="ANALYSIS RUNTIME" title="운영 분석 상태" />
      </div>
      <div className="analysis-status-grid">
        <article className={runtimeStatus?.advertising_model_available ? "active" : "pending"}>
          <span>광고·스팸 판정</span>
          <strong>{runtimeStatus?.advertising_model_available ? "운영 중" : "모델 연결 대기"}</strong>
          <small>{runtimeStatus?.advertising_model_available ? runtimeStatus.advertising_model_name : "광고·스팸 모델을 찾지 못했습니다."}</small>
        </article>
        <article className={runtimeStatus?.relevance_model_available ? "active" : "pending"}>
          <span>기업 관련성 판정</span>
          <strong>{runtimeStatus?.relevance_model_available ? "운영 중" : "모델 등록 대기"}</strong>
          <small>{runtimeStatus?.relevance_model_available ? runtimeStatus.relevance_model_name : "기업 관련성 모델을 찾지 못했습니다."}</small>
        </article>
        <article className={runtimeStatus?.sentiment_model_available ? "active" : "pending"}>
          <span>감성 분석</span>
          <strong>{runtimeStatus?.sentiment_model_available ? "운영 중" : "모델 연결 대기"}</strong>
          <small>{runtimeStatus?.sentiment_model_available ? runtimeStatus.sentiment_model_name : "감성 모델을 찾지 못했습니다."}</small>
        </article>
        <article className={riskStatus?.risk_detection_status === "available" ? "active" : "pending"}>
          <span>LightGBM 최종 위험 판정</span>
          <strong>{riskStatus?.risk_detection_status === "available" ? `${riskStatus.model_version ?? "LightGBM"} 운영 중` : "모델 등록 대기"}</strong>
          <small>{riskStatus?.message ?? runtimeStatus?.external_lightgbm_message ?? "운영 상태를 확인하고 있습니다."}</small>
        </article>
      </div>
    </section>
    <div className="model-layout model-layout-single">
      <section className="panel model-versions-panel"><PanelTitle kicker="ACTIVE MODEL RUNTIME" title="사용 중인 모델" /><div className="model-version-list">{displayedModels.length ? displayedModels.map((model) => <article className="model-version-row" key={model.id}><div><span className={`model-status ${model.status}`}>{MODEL_STATUS_LABELS[model.status] ?? model.status}</span><div><strong>{MODEL_TASK_LABELS[model.task] ?? model.task}</strong><small>{model.version} · {model.base_model || "사용자 정의 모델"}</small><small>{model.note ?? MODEL_TASK_DESCRIPTIONS[model.task]}</small></div></div><div><small>{model.runtime ? "로컬 런타임" : `등록 ${formatDate(model.created_at)}`}</small></div></article>) : <p className="panel-empty">현재 사용 중인 모델이 없습니다.</p>}</div></section>
    </div>
    <section className="panel collection-incidents-panel">
      <PanelTitle kicker="COLLECTION INCIDENTS" title="최근 수집 장애" />
      <IncidentList incidents={incidents} companies={companies} onAcknowledge={acknowledgeIncident} />
    </section>
    <section className="panel quality-operations model-quality"><div className="model-quality-head"><PanelTitle kicker="DAILY QUALITY CHECK" title="수집·분석 품질 점검" /><button className="secondary-button" type="button" onClick={rerunCheck} disabled={Boolean(busy)}>{busy === "check" ? "점검 중..." : "지금 다시 점검"}</button></div>{modelCheck ? <div className="daily-check-summary"><div><span>점검 상태</span><strong className={modelCheck.status}>{modelCheck.status === "stable" ? "안정" : modelCheck.status === "warning" ? "확인 필요" : "비교 자료 부족"}</strong></div><div><span>최근 특징 구간</span><strong>{formatNumber(modelCheck.report?.recent_window_count)}</strong></div><div><span>수집 커버리지</span><strong>{formatPercent(modelCheck.report?.collection_coverage)}</strong></div><div><span>분포 변화 경고</span><strong>{formatNumber(modelCheck.report?.drift_flags?.length)}</strong></div><small>마지막 점검 {formatDate(modelCheck.checked_at)}</small></div> : <p className="panel-empty">품질 점검 결과를 불러오는 중입니다.</p>}</section>
    <section className="panel quality-operations model-quality">
      <div className="model-quality-head">
        <PanelTitle kicker="LLM AUTO LABELING" title="LLM 자동 라벨링" />
        <div style={{ display: "flex", gap: 10 }}>
          <button className="secondary-button" type="button" onClick={() => navigate("/admin/reviews?mode=audit")}>월간 검수하러 가기</button>
          <button className="secondary-button" type="button" onClick={runLlmLabelingBacklog} disabled={Boolean(busy) || !llmLabeling?.enabled}>{busy === "llm-labeling" ? "실행 중..." : "밀린 기사 지금 처리"}</button>
        </div>
      </div>
      {llmLabeling ? <>
        <p className="dashboard-note">사람이 기사를 매 건 검수하는 대신, 별도의 LLM이 독립적으로 관련성·광고·감성을 판단해 정답 라벨로 바로 저장합니다. 수집될 때마다 자동으로 실행되며, "밀린 기사 지금 처리"는 놓친 기사를 수동으로 따라잡을 때만 씁니다.</p>
        <div className="daily-check-summary">
          <div><span>가동 상태</span><strong className={llmLabeling.enabled ? "stable" : "warning"}>{llmLabeling.enabled ? "자동 실행 중" : "API 키 필요"}</strong></div>
          <div><span>누적 라벨링</span><strong>{formatNumber(llmLabeling.llm_labeled_total)}건</strong></div>
          <div><span>최근 24시간</span><strong>{formatNumber(llmLabeling.llm_labeled_last_24h)}건</strong></div>
          <div><span>처리 대기(밀린 기사)</span><strong>{formatNumber(llmLabeling.pending_backlog)}건</strong></div>
          <div><span>월간 검수량</span><strong>{formatNumber(llmLabeling.audit.reviewed_count)}/{formatNumber(llmLabeling.audit.target_sample_size)}건</strong></div>
          <div><span>사람·LLM 일치율</span><strong>{formatPercent(llmLabeling.audit.agreement_rate)}</strong></div>
          <small>매달 무작위 표본을 사람이 블라인드로 다시 라벨링해 LLM 판단이 흔들리지 않는지 확인합니다.</small>
        </div>
      </> : <p className="panel-empty">LLM 라벨링 현황을 불러오는 중입니다.</p>}
    </section>
  </section>;
}
