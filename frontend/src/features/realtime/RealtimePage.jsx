import { useCallback, useEffect, useRef, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { IncidentList, Metric, Pagination, PanelTitle } from "../../shared/components";
import {
  DATA_QUALITY_LABELS,
  FILTERED_DATA_MODE,
  FILTER_REASON_LABELS,
  HEALTH_STATUS_LABELS,
  LIGHTGBM_STATE_LABELS,
  MONITORING_LABELS,
  READINESS_LABELS,
  REVIEW_DATA_MODE,
  RISK_TYPE_LABELS,
  SOURCE_LABELS,
  SUPPORTED_SOURCES,
  formatCountdown,
  formatDate,
  formatNumber,
  formatPercent,
  formatRiskProbability,
  formatScore,
  sentimentKind,
  sentimentText,
} from "../../shared/presentation";

// 거부 또는 검토 대기 중인 원문 기사와 필터 판정 근거를 표시한다.
function FilterResultRow({ result }) {
  const reasonText = FILTER_REASON_LABELS[result.reason] ?? result.reason;
  const decisionText = result.decision === "review_required" ? `${reasonText} 검토` : `${reasonText} 제외`;
  const methodText = result.classifier_kind === "rules_only" ? "규칙 판정" : "자동 판정";
  return <a className="article-row filter-result-row" href={result.url} target="_blank" rel="noreferrer">
    <span className={`filter-pill ${result.decision}`}>{decisionText}</span>
    <div><strong>{result.title}</strong>
      <small>{SOURCE_LABELS[result.source] ?? result.source} · 판정 {formatDate(result.filtered_at)}</small>
      <small className="filter-scores">관련성 {formatScore(result.relevance_score)} · 광고성 {formatScore(result.advertising_score)} · 신뢰도 {formatScore(result.confidence)} · {methodText}</small>
    </div>
  </a>;
}

// 최신 15분 특징 창과 수집 완전성, 공통 모델 상태를 요약한다.
function FeatureWindowSummary({ window: featureWindow }) {
  if (!featureWindow) return <p className="panel-empty">아직 생성된 15분 특징 구간이 없습니다.</p>;
  return <div className="feature-window-summary">
    <div className="feature-window-head"><div><span className="eyebrow">LATEST 15-MIN WINDOW</span><strong>{formatDate(featureWindow.window_start)}–{formatDate(featureWindow.window_end)}</strong></div><div><span className={`quality-pill ${featureWindow.data_quality}`}>{DATA_QUALITY_LABELS[featureWindow.data_quality]}</span><span className={`model-pill ${featureWindow.model_state}`}>{LIGHTGBM_STATE_LABELS[featureWindow.model_state] ?? "LightGBM 상태 확인 필요"}</span></div></div>
    <div className="window-metrics"><div><span>기사</span><strong>{formatNumber(featureWindow.article_count)}</strong></div><div><span>스토리</span><strong>{formatNumber(featureWindow.story_count)}</strong></div><div><span>확산</span><strong>{formatNumber(featureWindow.amplification_count)}</strong></div><div><span>언론사</span><strong>{formatNumber(featureWindow.publisher_count)}</strong></div><div title="운영 중인 LightGBM이 현재 구간의 최종 위험 가능성을 산출합니다."><span>위험도</span><strong>{formatRiskProbability(featureWindow.risk_probability)}</strong></div></div>
    {featureWindow.data_quality === "unavailable" && <p className="window-warning">수집 불가 구간이므로 위험도를 계산하지 않았습니다.</p>}
  </div>;
}

const HORIZON_LABELS = { immediate: "즉시", within_24h: "24시간 이내", within_7d: "7일 이내" };

function ActionGroups({ actions }) {
  return Object.entries(actions ?? {}).map(([horizon, items]) => <section className="scenario-actions" key={horizon}>
    <h5>{HORIZON_LABELS[horizon] ?? horizon}</h5>
    {(items ?? []).map((item, index) => <div className="scenario-action" key={`${horizon}-${index}`}><p>{typeof item === "string" ? item : item.action}</p>{typeof item !== "string" && item.evidence_urls?.map((url, urlIndex) => <a href={url} target="_blank" rel="noreferrer" key={url}>근거 {urlIndex + 1}</a>)}</div>)}
  </section>);
}

function ResponseDraftContent({ draft }) {
  const content = draft.content ?? {};
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const isCompetitorImpact = draft.generation_kind === "competitor_impact";
  return <div className="response-draft">
    <div className="response-draft-head"><div><span className="eyebrow">RESPONSE DRAFT · REVIEW REQUIRED</span><strong>{content.risk_summary}</strong></div><span className={`draft-kind ${isCompetitorImpact ? "competitor" : "main"}`}>{isCompetitorImpact ? "경쟁사 → 메인 기업 영향" : "메인 기업 직접 대응"}</span></div>
    {scenarios.length ? <div className="response-scenario-list">{scenarios.map((scenario, index) => <article className="response-scenario" key={`${scenario.title ?? "scenario"}-${index}`}>
      <header><span>경우 {String(index + 1).padStart(2, "0")}</span><h4>{scenario.title || `${index + 1}번째 대응안`}</h4></header>
      {scenario.assumption && <p><strong>전제</strong>{scenario.assumption}</p>}
      {scenario.possible_impact && <p><strong>메인 기업 예상 영향</strong>{scenario.possible_impact}</p>}
      {scenario.transmission_path && <p><strong>영향 전파 경로</strong>{scenario.transmission_path}</p>}
      {scenario.rationale && <p><strong>선택 근거</strong>{scenario.rationale}</p>}
      {scenario.early_indicators?.length > 0 && <div className="early-indicators"><strong>조기 관찰 지표</strong><ul>{scenario.early_indicators.map((indicator) => <li key={indicator}>{indicator}</li>)}</ul></div>}
      <ActionGroups actions={scenario.recommended_actions} />
    </article>)}</div> : <ActionGroups actions={content.recommended_actions} />}
    {content.uncertainty && <p className="uncertainty">불확실성: {content.uncertainty}</p>}
  </div>;
}

// 위험 이벤트의 근거·유형과 관리 승인이 필요한 대응 초안을 한곳에 표시한다.
function RiskDetail({ risk, canReview = false }) {
  const [drafts, setDrafts] = useState([]); const [loading, setLoading] = useState(false);
  const [notes, setNotes] = useState(""); const [error, setError] = useState(null);
  const loadDrafts = useCallback(async () => {
    if (!risk) { setDrafts([]); return; }
    try { const response = await api.get(`/risk-events/${risk.id}/response-drafts`); setDrafts(response.data); }
    catch (requestError) { setError(getErrorMessage(requestError)); }
  }, [risk]);
  useEffect(() => { loadDrafts(); }, [loadDrafts]);
  if (!risk) return <p className="panel-empty">확인할 위험 이벤트를 선택해 주세요.</p>;
  const latest = drafts[0]; const content = latest?.content;
  const generate = async () => { setLoading(true); setError(null); try { await api.post(`/risk-events/${risk.id}/response-drafts`); await loadDrafts(); } catch (requestError) { setError(getErrorMessage(requestError)); } finally { setLoading(false); } };
  const review = async (decision) => {
    if (!latest) return;
    setLoading(true); setError(null);
    try { await api.post(`/response-drafts/${latest.id}/${decision}`, { notes }); await loadDrafts(); }
    catch (requestError) { setError(getErrorMessage(requestError)); } finally { setLoading(false); }
  };
  return <div className="risk-detail">
    <div className="risk-detail-head"><div><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><h3>{risk.summary || risk.article_title || `위험 이벤트 #${risk.id}`}</h3></div><span className={`model-pill ${risk.model_state}`}>{LIGHTGBM_STATE_LABELS[risk.model_state] ?? "LightGBM 상태 확인 필요"}</span></div>
    <p>위험도 {formatRiskProbability(risk.risk_probability)} · 이상 점수 {formatScore(risk.anomaly_score)} · {formatDate(risk.detected_at)}</p>
    <div className="risk-type-list">{risk.risk_types.map((item) => <span key={item.risk_type}>{RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type} {formatPercent(item.probability)}</span>)}</div>
    <div className="evidence-list"><strong>근거 기사</strong>{risk.evidence_articles.length ? risk.evidence_articles.map((article) => <a key={article.article_id} href={article.url} target="_blank" rel="noreferrer">{article.title}</a>) : <small>연결된 근거 기사가 없습니다.</small>}</div>
    {!latest && <button className="secondary-button" type="button" onClick={generate} disabled={loading || !risk.evidence_articles.length}>{loading ? "생성 중..." : "근거 기반 대응 초안 생성"}</button>}
    {error && <div className="notice error">{error}</div>}
    {content && <><ResponseDraftContent draft={latest} />{canReview ? <div className="draft-review"><input value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="검토 메모 (선택)" /><button type="button" onClick={() => review("approve")} disabled={loading || latest.approval_state !== "draft"}>승인</button><button type="button" onClick={() => review("reject")} disabled={loading || latest.approval_state !== "draft"}>반려</button><span>{latest.approval_state === "draft" ? "외부 전송·실행 금지" : latest.approval_state === "approved" ? `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}승인 완료` : `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}반려됨`}</span></div> : <div className="draft-review readonly"><span>{latest.approval_state === "draft" ? "멤버 승인 대기" : latest.approval_state === "approved" ? "승인 완료" : "반려됨"}</span></div>}</>}
  </div>;
}

// 일별 기사 수와, 운영 LightGBM이 준비된 경우에만 위험 수를 겹친 선 그래프로 표시한다.
function OverlayLineChart({ overview, riskAvailable = true }) {
  const items = overview?.daily ?? [];
  if (!items.length) return <p className="panel-empty">최근 7일간 표시할 수집 데이터가 없습니다.</p>;

  // 고정 viewBox 안에서 기사 수와 위험 수를 각각 독립적인 최대값으로 정규화한다.
  const width = 900; const height = 280;
  const left = 52; const right = 52; const top = 24; const bottom = 42;
  const plotWidth = width - left - right; const plotHeight = height - top - bottom;
  const collectionMax = Math.max(...items.map((item) => item.article_count), 1);
  const riskMax = Math.max(...items.map((item) => item.risk_count), 1);
  // 일별 데이터 인덱스를 SVG 가로 좌표로 변환한다.
  const x = (index) => left + (items.length === 1 ? plotWidth / 2 : index / (items.length - 1) * plotWidth);
  // 기사 수를 왼쪽 축 기준 SVG 세로 좌표로 변환한다.
  const collectionY = (value) => top + plotHeight - value / collectionMax * plotHeight;
  // 위험 수를 오른쪽 축 기준 SVG 세로 좌표로 변환한다.
  const riskY = (value) => top + plotHeight - value / riskMax * plotHeight;
  const collectionPoints = items.map((item, index) => `${x(index)},${collectionY(item.article_count)}`).join(" ");
  const riskPoints = items.map((item, index) => `${x(index)},${riskY(item.risk_count)}`).join(" ");

  return <div className="overlay-chart">
    <div className="trend-legend"><span className="collection-line">수집량 <strong>{formatNumber(overview.article_count)}건</strong></span><span className="risk-line">위험량 <strong>{riskAvailable ? `${formatNumber(overview.risk_count)}건` : "판정 대기"}</strong></span><small>{riskAvailable ? "수집량 좌측 축 · 위험량 우측 축" : "운영 LightGBM 준비 후 위험 추세 제공"}</small></div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={riskAvailable ? "최근 7일 전체 수집량과 위험량 선 그래프" : "최근 7일 전체 수집량 선 그래프, 위험량은 판정 대기"}>
      {[0, .25, .5, .75, 1].map((ratio) => { const y = top + ratio * plotHeight; return <g key={ratio}><line className="trend-grid-line" x1={left} x2={width - right} y1={y} y2={y} /><text className="trend-axis-label" x={left - 10} y={y + 4} textAnchor="end">{Math.round(collectionMax * (1 - ratio))}</text>{riskAvailable && <text className="trend-axis-label risk-axis-label" x={width - right + 10} y={y + 4}>{Math.round(riskMax * (1 - ratio))}</text>}</g>; })}
      <polyline className="trend-line collection" points={collectionPoints} />
      {riskAvailable && <polyline className="trend-line risk" points={riskPoints} />}
      {items.map((item, index) => <g key={item.day}><circle className="trend-dot collection" cx={x(index)} cy={collectionY(item.article_count)} r="4" />{riskAvailable && <circle className="trend-dot risk" cx={x(index)} cy={riskY(item.risk_count)} r="4" />}<text className="trend-date-label" x={x(index)} y={height - 13} textAnchor="middle">{new Date(item.day).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}</text></g>)}
    </svg>
  </div>;
}

// 기업별 실시간 수집 현황, 기사, 위험 이벤트와 제어 기능을 제공한다.
export default function RealtimePage({ initialCompanyId, initialRiskEventId = null, canAdminister = false, onCompanyChange }) {
  const [companies, setCompanies] = useState([]); const [selectedId, setSelectedId] = useState(initialCompanyId ? String(initialCompanyId) : "");
  const [data, setData] = useState(null); const [error, setError] = useState(null);
  const [articlePage, setArticlePage] = useState(1);
  const [riskPage, setRiskPage] = useState(1);
  const [displayMode, setDisplayMode] = useState("");
  const [articleSources, setArticleSources] = useState([]);
  const [changingState, setChangingState] = useState(false);
  const [bulkChangingState, setBulkChangingState] = useState(null);
  const [selectedRiskId, setSelectedRiskId] = useState(initialRiskEventId);
  const [activating, setActivating] = useState(false);
  const [now, setNow] = useState(Date.now());
  const refreshSequence = useRef(0);
  const riskDetailRef = useRef(null);
  // 선택 기업의 모니터링·기사·위험·대시보드·필터 데이터를 병렬로 갱신한다.
  const refresh = useCallback(async () => {
    const requestId = ++refreshSequence.current;
    try {
      const companyResponse = await api.get("/companies"); const nextCompanies = companyResponse.data;
      if (requestId !== refreshSequence.current) return;
      setCompanies(nextCompanies);
      const requestedCompany = nextCompanies.find((company) => String(company.id) === String(selectedId));
      const id = requestedCompany?.id ?? nextCompanies[0]?.id;
      if (!id) { setData(null); return; }
      if (String(id) !== String(selectedId)) {
        setSelectedId(String(id));
        onCompanyChange?.(id, { replace: true });
      }
      const filterDecision = displayMode === FILTERED_DATA_MODE
        ? "rejected"
        : displayMode === REVIEW_DATA_MODE
          ? "review_required"
          : null;
      const sourceQuery = displayMode && !filterDecision ? `&source=${encodeURIComponent(displayMode)}` : "";
      const articleRequest = filterDecision
        ? api.get(`/companies/${id}/filter-results?decision=${filterDecision}&page=${articlePage}&page_size=10`)
        : api.get(`/companies/${id}/articles?page=${articlePage}&page_size=10${sourceQuery}`);
      const [monitoring, articles, risks, overview, filtering, windows, health, incidents] = await Promise.all([
        api.get(`/companies/${id}/monitoring`), articleRequest, api.get(`/companies/${id}/risk-events?limit=200`),
        api.get("/dashboard/overview?days=7"), api.get(`/companies/${id}/filter-summary`),
        api.get(`/companies/${id}/feature-windows?limit=96`), api.get("/collection-health"),
        api.get("/collection-incidents?page=1&page_size=20"),
      ]);
      if (requestId !== refreshSequence.current) return;
      if (!filterDecision) setArticleSources(articles.data.sources);
      setData({ monitoring: monitoring.data, articles: articles.data, articleKind: filterDecision ? "filter_results" : "articles", articleDecision: filterDecision, risks: risks.data, overview: overview.data, filtering: filtering.data, windows: windows.data, health: health.data, incidents: incidents.data.items }); setError(null);
    } catch (requestError) { if (requestId === refreshSequence.current) setError(getErrorMessage(requestError)); }
  }, [selectedId, articlePage, displayMode, onCompanyChange]);
  // 수집 주기보다 빠른 30초 간격으로 서버 현황을 다시 조회한다.
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 30000); return () => { window.clearInterval(timer); refreshSequence.current += 1; }; }, [refresh]);
  // 서버 요청 없이 카운트다운 표시만 매초 다시 계산하도록 현재 시각을 갱신한다.
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const selected = companies.find((company) => String(company.id) === String(selectedId));
  // API에서 가져온 최근 위험 200건은 추가 요청 없이 화면에서 10건씩 나눠 보여 준다.
  const riskPageSize = 10;
  const visibleRisks = data?.risks.slice((riskPage - 1) * riskPageSize, riskPage * riskPageSize) ?? [];
  const selectedRisk = data?.risks.find((risk) => risk.id === selectedRiskId) ?? visibleRisks[0] ?? null;
  useEffect(() => { setSelectedRiskId(initialRiskEventId); }, [initialRiskEventId]);
  useEffect(() => {
    if (!initialRiskEventId || selectedRisk?.id !== initialRiskEventId || !riskDetailRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      riskDetailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      riskDetailRef.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [initialRiskEventId, selectedRisk?.id]);
  const latestWindow = data?.windows?.[0] ?? null;
  const secondsUntilCollection = data?.monitoring.monitoring_status === "paused"
    ? data.monitoring.collection_interval_seconds
    : data?.monitoring.next_collection_at
      ? (new Date(data.monitoring.next_collection_at).getTime() - now) / 1000
      : null;
  // 백엔드 상태와 진행 중인 요청을 조합해 제어 버튼의 동작·표시 상태를 계산한다.
  const monitoringStatus = selected?.monitoring_status;
  const startingMonitoring = changingState && monitoringStatus === "paused";
  const monitoringPreparing = startingMonitoring || ["backfilling", "warming"].includes(monitoringStatus);
  const monitoringActionAvailable = ["paused", "active"].includes(monitoringStatus);
  const showCollectionCountdown = ["paused", "active"].includes(monitoringStatus)
    && Boolean(data?.monitoring.next_collection_at);
  const monitorControlClass = monitoringPreparing ? "preparing" : monitoringStatus === "paused" ? "start" : "stop";
  const monitorControlLabel = monitoringPreparing
    ? "실시간 모니터링 준비 중"
    : changingState
      ? "처리 중..."
      : monitoringStatus === "paused"
        ? "실시간 모니터링 시작"
        : monitoringStatus === "active"
          ? "실시간 모니터링 중지"
          : "설정 확인 필요";
  const showingFilterResults = data?.articleKind === "filter_results";
  const articlePanelTitle = data?.articleDecision === "review_required"
    ? "검토 필요한 데이터"
    : showingFilterResults
      ? "필터링된 데이터"
      : "수집된 기사";
  const articleEmptyText = data?.articleDecision === "review_required"
    ? "검토가 필요한 데이터가 없습니다."
    : showingFilterResults
      ? "필터로 제외된 데이터가 없습니다."
      : "선택한 조건에 맞는 기사가 없습니다.";
  // 선택 기업의 실시간 모니터링을 현재 상태에 따라 중지하거나 재개한다.
  const changeMonitoringState = async () => {
    if (!selected || !monitoringActionAvailable) return;
    setChangingState(true);
    try {
      await api.post(`/companies/${selected.id}/monitoring/${monitoringStatus === "paused" ? "resume" : "pause"}`);
      await refresh();
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setChangingState(false); }
  };
  // 모든 기업의 모니터링 상태를 지정한 동작으로 일괄 변경한다.
  const changeAllMonitoringStates = async (action) => {
    const actionLabel = action === "pause" ? "모든 기업의 실시간 모니터링을 중지" : "모든 기업의 실시간 모니터링을 재개";
    if (!window.confirm(`${actionLabel}할까요?`)) return;
    setBulkChangingState(action); setError(null);
    try {
      await api.post(`/companies/monitoring/bulk/${action}`);
      await refresh();
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBulkChangingState(null); }
  };
  const activateSelected = async () => {
    if (!selected || selected.readiness_status !== "pending_approval") return;
    if (!window.confirm(`${selected.name}의 모니터링을 승인하고 시작할까요?`)) return;
    setActivating(true); setError(null);
    try { await api.post(`/companies/${selected.id}/activate`); await refresh(); }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setActivating(false); }
  };
  const acknowledgeIncident = async (incidentId) => {
    try { await api.post(`/collection-incidents/${incidentId}/acknowledge`); await refresh(); }
    catch (requestError) { setError(getErrorMessage(requestError)); }
  };
  return <section className="workspace"><div className="workspace-head"><div><span className="eyebrow">COMPANY DETAIL / 04</span><h1>기업 상세</h1><p>기업별 수집 통계, 위험 근거와 대응 초안을 한곳에서 확인합니다.</p></div><span className="live-indicator"><i /> LIVE</span></div>
    {canAdminister && <div className="bulk-monitor-controls"><button className="monitor-control stop" type="button" onClick={() => changeAllMonitoringStates("pause")} disabled={Boolean(bulkChangingState)}>{bulkChangingState === "pause" ? "중지 중..." : "전체 중지"}</button><button className="monitor-control start" type="button" onClick={() => changeAllMonitoringStates("resume")} disabled={Boolean(bulkChangingState)}>{bulkChangingState === "resume" ? "재개 중..." : "전체 모니터링 재개"}</button></div>}
    <div className="monitor-toolbar"><label>상세 기업<select value={selectedId} onChange={(event) => { const nextCompanyId = event.target.value; setSelectedId(nextCompanyId); setArticlePage(1); setRiskPage(1); setSelectedRiskId(null); setDisplayMode(""); setArticleSources([]); onCompanyChange?.(nextCompanyId); }}>{companies.map((company) => <option key={company.id} value={company.id}>{company.name} · {company.industry_name}</option>)}</select></label>{showCollectionCountdown && <div className="collection-countdown"><span>다음 기사 수집까지</span><strong>{formatCountdown(secondsUntilCollection)}</strong><small>15분 주기</small></div>}{selected && <><span className={`state-badge ${selected.monitoring_status}`}>{MONITORING_LABELS[selected.monitoring_status]}</span>{canAdminister && <button className={`monitor-control ${monitorControlClass}`} onClick={changeMonitoringState} disabled={changingState || bulkChangingState || monitoringPreparing || !monitoringActionAvailable}>{monitoringPreparing && <i className="monitor-loader" aria-hidden="true" />}{monitorControlLabel}</button>}</>}</div>
    {error && <div className="notice error">{error}</div>}
    {!selected ? <p className="empty-state">먼저 기업 등록 페이지에서 모니터링할 기업을 등록해 주세요.</p> : data && <>
      <section className={`readiness-banner ${data.monitoring.readiness_status}`}><div><strong>{READINESS_LABELS[data.monitoring.readiness_status]}</strong><span>{data.monitoring.readiness_status === "active" ? `관련 기사 ${formatNumber(data.monitoring.accepted_article_count)}건 · 유효한 비어 있지 않은 구간 ${formatNumber(data.monitoring.valid_nonempty_window_count)}개` : `관련 기사 ${formatNumber(data.monitoring.accepted_article_count)}/50 · 유효한 비어 있지 않은 구간 ${formatNumber(data.monitoring.valid_nonempty_window_count)}/40`}</span></div><div><span className={`model-pill ${data.monitoring.model_state}`}>{LIGHTGBM_STATE_LABELS[data.monitoring.model_state] ?? "LightGBM 상태 확인 필요"}</span>{canAdminister && data.monitoring.readiness_status === "pending_approval" && <button type="button" onClick={activateSelected} disabled={activating}>{activating ? "활성화 중..." : "승인하고 모니터링 시작"}</button>}</div></section>
      <div className="metric-grid"><Metric label="정제 통과 기사" value={data.monitoring.article_count} /><Metric label="감성 분석 완료" value={data.monitoring.analyzed_count} /><Metric label="마지막 수집" value={formatDate(data.monitoring.last_collected_at)} small /></div>
      <section className="filter-summary"><div><span>수집 원문</span><strong>{formatNumber(data.filtering.raw_count)}</strong></div><div><span>중복 병합</span><strong>{formatNumber(data.filtering.duplicate_count)}</strong></div><div><span>광고 제외</span><strong>{formatNumber(data.filtering.advertisement_count)}</strong></div><div><span>무관 제외</span><strong>{formatNumber(data.filtering.irrelevant_count)}</strong></div><div><span>검토 필요</span><strong>{formatNumber(data.filtering.review_required_count)}</strong></div><div><span>자동 판정</span><strong>{formatNumber(data.filtering.ai_assisted_count)}</strong></div></section>
      <FeatureWindowSummary window={latestWindow} />
      <div className="live-grid"><section className="panel span-two"><PanelTitle kicker={showingFilterResults ? "FILTER RESULTS" : "COLLECTED ARTICLES"} title={articlePanelTitle} /><label className="source-filter">표시할 데이터<select value={displayMode} onChange={(event) => { setDisplayMode(event.target.value); setArticlePage(1); }}><option value="">정제 통과 기사 전체</option><optgroup label="필터 판정"><option value={FILTERED_DATA_MODE}>필터 제외 데이터 · {formatNumber(data.filtering.rejected_count)}건</option><option value={REVIEW_DATA_MODE}>검토 필요 데이터 · {formatNumber(data.filtering.review_required_count)}건</option></optgroup><optgroup label="정제 통과 기사 출처">{articleSources.map((source) => <option value={source} key={source}>{SOURCE_LABELS[source] ?? source}</option>)}{SUPPORTED_SOURCES.filter((source) => !articleSources.includes(source)).map((source) => <option value={source} key={source} disabled>{SOURCE_LABELS[source]} · 수집 데이터 없음</option>)}</optgroup></select></label><div className="article-list">{data.articles.items.length ? (showingFilterResults ? data.articles.items.map((result) => <FilterResultRow result={result} key={result.id} />) : data.articles.items.map((article) => <a className="article-row" key={article.id} href={article.url} target="_blank" rel="noreferrer"><span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span><div><strong>{article.title}</strong><small>{SOURCE_LABELS[article.source] ?? article.source} · {formatDate(article.published_at ?? article.created_at)}</small></div></a>)) : <p className="panel-empty">{articleEmptyText}</p>}</div><Pagination page={data.articles.page} pageSize={data.articles.page_size} total={data.articles.total} onChange={setArticlePage} /></section>
        <section className="panel"><PanelTitle kicker="RISK EVENTS" title="기업 위험 이벤트" /><div className="risk-list selectable">{visibleRisks.length ? visibleRisks.map((risk) => <button className={selectedRisk?.id === risk.id ? "selected" : ""} type="button" onClick={() => setSelectedRiskId(risk.id)} key={risk.id}><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><strong>{risk.summary || risk.article_title || `위험 이벤트 #${risk.id}`}</strong><small>위험도 {formatRiskProbability(risk.risk_probability)} · {formatDate(risk.detected_at)}</small></button>) : <p className="panel-empty">새 위험 이벤트가 없습니다.</p>}</div><Pagination page={riskPage} pageSize={riskPageSize} total={data.risks.length} onChange={setRiskPage} /></section>
      </div>
      {selectedRisk && <section className="panel risk-detail-panel" ref={riskDetailRef} tabIndex={-1}><PanelTitle kicker="EVIDENCE & RESPONSE" title="위험 근거와 대응 초안" /><RiskDetail risk={selectedRisk} canReview={canAdminister} /></section>}
      <div className="operations-grid"><section className="panel"><PanelTitle kicker="COLLECTION HEALTH" title="수집 시스템 상태" /><div className={`health-state ${data.health.status}`}><strong>{data.health.status === "healthy" ? "정상" : data.health.status === "degraded" ? "일부 장애" : data.health.status === "unavailable" ? "수집 불가" : "확인 전"}</strong><span>열린 장애 {formatNumber(data.health.open_incident_count)}건</span></div><div className="source-health-list">{data.health.sources.map((source) => <div key={source.source}><span>{SOURCE_LABELS[source.source] ?? source.source}</span><strong className={source.status}>{HEALTH_STATUS_LABELS[source.status] ?? source.status}</strong><small>연속 실패 {source.consecutive_failures}회</small></div>)}</div></section><section className="panel"><PanelTitle kicker="COLLECTION INCIDENTS" title="수집 장애" /><IncidentList incidents={data.incidents} companies={companies} onAcknowledge={canAdminister ? acknowledgeIncident : undefined} /></section></div>
      <section className="panel realtime-trend-panel"><PanelTitle kicker="TOTAL TREND / 7 DAYS" title={data.monitoring.model_state === "production" ? "전체 수집량 · 위험량" : "전체 수집량 · 위험 판정 대기"} /><OverlayLineChart overview={data.overview} riskAvailable={data.monitoring.model_state === "production"} /></section>
    </>}
  </section>;
}
