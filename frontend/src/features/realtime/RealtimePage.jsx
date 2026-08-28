import { useCallback, useEffect, useRef, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { Metric, Pagination, PanelTitle } from "../../shared/components";
import {
  FILTERED_DATA_MODE,
  LIGHTGBM_STATE_LABELS,
  MONITORING_LABELS,
  READINESS_LABELS,
  REVIEW_DATA_MODE,
  SOURCE_LABELS,
  SUPPORTED_SOURCES,
  formatCountdown,
  formatDate,
  formatNumber,
  formatRiskProbability,
  sentimentKind,
  sentimentText,
} from "../../shared/presentation";
import { FeatureWindowSummary, FilterResultRow, RiskDetail } from "./RealtimePanels";

// 기업별 실시간 수집 현황, 기사, 위험 이벤트와 제어 기능을 제공한다.
export default function RealtimePage({ initialCompanyId, initialRiskEventId = null, canAdminister = false, onCompanyChange, onEditCompany }) {
  const [companies, setCompanies] = useState([]); const [selectedId, setSelectedId] = useState(initialCompanyId ? String(initialCompanyId) : "");
  const [data, setData] = useState(null); const [error, setError] = useState(null);
  const [articlePage, setArticlePage] = useState(1);
  const [riskPage, setRiskPage] = useState(1);
  const [displayMode, setDisplayMode] = useState("");
  const [articleSources, setArticleSources] = useState([]);
  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [changingState, setChangingState] = useState(false);
  const [bulkChangingState, setBulkChangingState] = useState(null);
  const [selectedRiskId, setSelectedRiskId] = useState(initialRiskEventId);
  const [activating, setActivating] = useState(false);
  const [now, setNow] = useState(Date.now());
  const refreshSequence = useRef(0);
  const riskDetailRef = useRef(null);
  // 입력 즉시 요청을 보내는 대신, 타이핑이 잠시 멈췄을 때만 검색어를 반영한다.
  useEffect(() => {
    const timer = window.setTimeout(() => setSearchQuery(searchInput.trim()), 400);
    return () => window.clearTimeout(timer);
  }, [searchInput]);
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
      const searchFilterQuery = !filterDecision
        ? `${searchQuery ? `&q=${encodeURIComponent(searchQuery)}` : ""}${dateFrom ? `&date_from=${dateFrom}` : ""}${dateTo ? `&date_to=${dateTo}` : ""}`
        : "";
      const articleRequest = filterDecision
        ? api.get(`/companies/${id}/filter-results?decision=${filterDecision}&page=${articlePage}&page_size=10`)
        : api.get(`/companies/${id}/articles?page=${articlePage}&page_size=10${sourceQuery}${searchFilterQuery}`);
      const [monitoring, articles, risks, filtering, windows] = await Promise.all([
        api.get(`/companies/${id}/monitoring`), articleRequest, api.get(`/companies/${id}/risk-events?limit=200`),
        api.get(`/companies/${id}/filter-summary`), api.get(`/companies/${id}/feature-windows?limit=96`),
      ]);
      if (requestId !== refreshSequence.current) return;
      if (!filterDecision) setArticleSources(articles.data.sources);
      setData({ monitoring: monitoring.data, articles: articles.data, articleKind: filterDecision ? "filter_results" : "articles", articleDecision: filterDecision, risks: risks.data, filtering: filtering.data, windows: windows.data }); setError(null);
    } catch (requestError) { if (requestId === refreshSequence.current) setError(getErrorMessage(requestError)); }
  }, [selectedId, articlePage, displayMode, searchQuery, dateFrom, dateTo, onCompanyChange]);
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
  return <section className="workspace realtime-workspace"><div className="workspace-head"><div><span className="eyebrow">COMPANY DETAIL / 04</span><h1>{selected ? <button className="company-name-link main-company-name" type="button" onClick={() => onEditCompany?.(selected.id)}>{selected.name}</button> : "기업 상세"}</h1><p className="main-lead">수집 통계, 이상 탐지·위험 근거와 대응 초안을 한 화면에서 확인합니다.</p></div><span className="live-indicator"><i /> LIVE</span></div>
    {canAdminister && <div className="bulk-monitor-controls"><button className="monitor-control stop" type="button" onClick={() => changeAllMonitoringStates("pause")} disabled={Boolean(bulkChangingState)}>{bulkChangingState === "pause" ? "중지 중..." : "전체 중지"}</button><button className="monitor-control start" type="button" onClick={() => changeAllMonitoringStates("resume")} disabled={Boolean(bulkChangingState)}>{bulkChangingState === "resume" ? "재개 중..." : "전체 모니터링 재개"}</button></div>}
    <div className="monitor-toolbar">{showCollectionCountdown && <div className="collection-countdown"><span>다음 기사 수집까지</span><strong>{formatCountdown(secondsUntilCollection)}</strong><small>15분 주기</small></div>}{selected && <><span className={`state-badge ${selected.monitoring_status}`}>{MONITORING_LABELS[selected.monitoring_status]}</span>{canAdminister && <button className={`monitor-control ${monitorControlClass}`} onClick={changeMonitoringState} disabled={changingState || bulkChangingState || monitoringPreparing || !monitoringActionAvailable}>{monitoringPreparing && <i className="monitor-loader" aria-hidden="true" />}{monitorControlLabel}</button>}</>}</div>
    {error && <div className="notice error">{error}</div>}
    {!selected ? <p className="empty-state">먼저 기업 등록 페이지에서 모니터링할 기업을 등록해 주세요.</p> : data && <>
      <section className={`readiness-banner ${data.monitoring.readiness_status}`}><div><strong>{READINESS_LABELS[data.monitoring.readiness_status]}</strong><span>{data.monitoring.readiness_status === "active" ? `관련 기사 ${formatNumber(data.monitoring.accepted_article_count)}건 · 유효한 비어 있지 않은 구간 ${formatNumber(data.monitoring.valid_nonempty_window_count)}개` : `관련 기사 ${formatNumber(data.monitoring.accepted_article_count)}/50 · 유효한 비어 있지 않은 구간 ${formatNumber(data.monitoring.valid_nonempty_window_count)}/40`}</span></div><div><span className={`model-pill ${data.monitoring.model_state}`}>{LIGHTGBM_STATE_LABELS[data.monitoring.model_state] ?? "LightGBM 상태 확인 필요"}</span>{canAdminister && data.monitoring.readiness_status === "pending_approval" && <button type="button" onClick={activateSelected} disabled={activating}>{activating ? "활성화 중..." : "승인하고 모니터링 시작"}</button>}</div></section>
      <div className="metric-grid realtime-metrics"><Metric label="정제 통과 기사" value={data.monitoring.article_count} /><Metric label="감성 분석 완료" value={data.monitoring.analyzed_count} /><Metric label="이상치 탐지" value={data.monitoring.anomaly_count} /><Metric label="마지막 수집" value={formatDate(data.monitoring.last_collected_at)} small /></div>
      <section className="filter-summary"><div><span>수집 원문</span><strong>{formatNumber(data.filtering.raw_count)}</strong></div><div><span>중복 병합</span><strong>{formatNumber(data.filtering.duplicate_count)}</strong></div><div><span>광고 제외</span><strong>{formatNumber(data.filtering.advertisement_count)}</strong></div><div><span>무관 제외</span><strong>{formatNumber(data.filtering.irrelevant_count)}</strong></div><div><span>검토 필요</span><strong>{formatNumber(data.filtering.review_required_count)}</strong></div><div><span>자동 판정</span><strong>{formatNumber(data.filtering.ai_assisted_count)}</strong></div></section>
      <FeatureWindowSummary window={latestWindow} />
      <div className="live-grid"><section className="panel span-two"><PanelTitle kicker={showingFilterResults ? "FILTER RESULTS" : "COLLECTED ARTICLES"} title={articlePanelTitle} /><div className="article-controls"><label className="source-filter">표시할 데이터<select value={displayMode} onChange={(event) => { setDisplayMode(event.target.value); setArticlePage(1); }}><option value="">정제 통과 기사 전체</option><optgroup label="필터 판정"><option value={FILTERED_DATA_MODE}>필터 제외 데이터 · {formatNumber(data.filtering.rejected_count)}건</option><option value={REVIEW_DATA_MODE}>검토 필요 데이터 · {formatNumber(data.filtering.review_required_count)}건</option></optgroup><optgroup label="정제 통과 기사 출처">{articleSources.map((source) => <option value={source} key={source}>{SOURCE_LABELS[source] ?? source}</option>)}{SUPPORTED_SOURCES.filter((source) => !articleSources.includes(source)).map((source) => <option value={source} key={source} disabled>{SOURCE_LABELS[source]} · 수집 데이터 없음</option>)}</optgroup></select></label>{!showingFilterResults && <><label className="article-date-filter">시작일<input type="date" value={dateFrom} max={dateTo || undefined} onChange={(event) => { setDateFrom(event.target.value); setArticlePage(1); }} /></label><label className="article-date-filter">종료일<input type="date" value={dateTo} min={dateFrom || undefined} onChange={(event) => { setDateTo(event.target.value); setArticlePage(1); }} /></label><label className="article-search">검색<input type="search" value={searchInput} onChange={(event) => { setSearchInput(event.target.value); setArticlePage(1); }} placeholder="제목·요약으로 검색" /></label></>}</div><div className="article-list scroll-box">{data.articles.items.length ? (showingFilterResults ? data.articles.items.map((result) => <FilterResultRow result={result} key={result.id} />) : data.articles.items.map((article) => <a className="article-row" key={article.id} href={article.url} target="_blank" rel="noreferrer"><span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span><div><strong>{article.title}</strong><small>{SOURCE_LABELS[article.source] ?? article.source} · {formatDate(article.published_at ?? article.created_at)}</small></div></a>)) : <p className="panel-empty">{articleEmptyText}</p>}</div><Pagination page={data.articles.page} pageSize={data.articles.page_size} total={data.articles.total} onChange={setArticlePage} /></section>
        <section className="panel"><PanelTitle kicker="RISK EVENTS" title="기업 위험 이벤트" /><div className="risk-list selectable scroll-box">{visibleRisks.length ? visibleRisks.map((risk) => <button className={selectedRisk?.id === risk.id ? "selected" : ""} type="button" onClick={() => setSelectedRiskId(risk.id)} key={risk.id}><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><strong>{risk.summary || risk.article_title || `위험 이벤트 #${risk.id}`}</strong><small>위험도 {formatRiskProbability(risk.risk_probability)} · {formatDate(risk.detected_at)}</small></button>) : <p className="panel-empty">새 위험 이벤트가 없습니다.</p>}</div><Pagination page={riskPage} pageSize={riskPageSize} total={data.risks.length} onChange={setRiskPage} /></section>
      </div>
      {selectedRisk && <section className="panel risk-detail-panel" ref={riskDetailRef} tabIndex={-1}><PanelTitle kicker="EVIDENCE & RESPONSE" title="위험 근거와 대응 초안" /><div className="risk-detail-scroll"><RiskDetail risk={selectedRisk} canReview={canAdminister} /></div></section>}
    </>}
  </section>;
}
