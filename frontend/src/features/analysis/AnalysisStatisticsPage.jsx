import { useCallback, useEffect, useRef, useState } from "react";

import { api, getErrorMessage } from "../../api";
import MainResponseContent from "./MainResponseContent";
import { Pagination, PanelTitle } from "../../shared/components";
import {
  DATA_QUALITY_LABELS,
  FILTERED_DATA_MODE,
  FILTER_REASON_LABELS,
  MONITORING_LABELS,
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

const STATISTICS_GRAPH_WIDTH = 720;
const STATISTICS_GRAPH_HEIGHT = 230;
const STATISTICS_GRAPH_PADDING = { top: 18, right: 12, bottom: 24, left: 8 };
const SEOUL_DAY_FORMATTER = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" });

const seoulDayKey = (value) => {
  const parts = Object.fromEntries(SEOUL_DAY_FORMATTER.formatToParts(new Date(value)).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
};

// 일일 요약의 기사 합계와 같은 날짜에 기록된 최고 위험도를 하나의 그래프 점으로 결합한다.
function buildDailyTrendPoints(dailySummaries, windows) {
  const riskByDay = new Map();
  (windows ?? []).forEach((window) => {
    if (window.risk_probability === null || window.risk_probability === undefined) return;
    const day = seoulDayKey(window.window_start);
    riskByDay.set(day, Math.max(riskByDay.get(day) ?? 0, window.risk_probability));
  });
  return (dailySummaries ?? []).map((summary) => ({
    id: `daily-${summary.summary_date}`,
    window_start: `${summary.summary_date}T00:00:00+09:00`,
    article_count: summary.article_count,
    risk_probability: riskByDay.get(summary.summary_date) ?? 0,
  }));
}

// 분석 통계의 날짜별 총수집량과 일일 최고 위험도 흐름을 보여준다.
function DetailTrendGraph({ windows, label }) {
  const points = [...(windows ?? [])].reverse();
  const articleValues = points.map((item) => item.article_count ?? 0);
  const riskValues = points.map((item) => (item.risk_probability ?? 0) * 100);
  const articleMax = Math.max(...articleValues, 1);
  const plotWidth = STATISTICS_GRAPH_WIDTH - STATISTICS_GRAPH_PADDING.left - STATISTICS_GRAPH_PADDING.right;
  const plotHeight = STATISTICS_GRAPH_HEIGHT - STATISTICS_GRAPH_PADDING.top - STATISTICS_GRAPH_PADDING.bottom;
  const point = (value, max, index) => {
    const x = STATISTICS_GRAPH_PADDING.left + (points.length < 2 ? plotWidth / 2 : index / (points.length - 1) * plotWidth);
    const y = STATISTICS_GRAPH_PADDING.top + plotHeight - value / max * plotHeight;
    return `${x},${y}`;
  };
  const articlePoints = points.map((item, index) => point(item.article_count ?? 0, articleMax, index)).join(" ");
  const riskPoints = points.map((item, index) => point((item.risk_probability ?? 0) * 100, 100, index)).join(" ");
  const tickIndexes = points.length ? [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])] : [];
  const formatWindowDate = (value) => value ? new Date(value).toLocaleDateString("ko-KR", { month: "2-digit", day: "2-digit" }) : "-";
  return <section className="home-trend-graphs statistics-trend-graphs" aria-label={`${label} 일별 수집량과 위험도 추세`}>
    <figure className="home-line-graph">
      <figcaption><span className="collection">오늘 수집량 <strong>{articleValues.at(-1) ?? 0}</strong></span><span className="risk">오늘 최고 위험도 <strong>{formatPercent((riskValues.at(-1) ?? 0) / 100)}</strong></span></figcaption>
      {points.length ? <svg viewBox={`0 0 ${STATISTICS_GRAPH_WIDTH} ${STATISTICS_GRAPH_HEIGHT}`} role="img" aria-label={`${label} 일별 수집량과 최고 위험도 그래프`}>
        {[0, .5, 1].map((ratio) => <line key={ratio} className="home-graph-grid" x1={STATISTICS_GRAPH_PADDING.left} x2={STATISTICS_GRAPH_WIDTH - STATISTICS_GRAPH_PADDING.right} y1={STATISTICS_GRAPH_PADDING.top + plotHeight * ratio} y2={STATISTICS_GRAPH_PADDING.top + plotHeight * ratio} />)}
        <polyline className="home-graph-line collection" points={articlePoints} />
        <polyline className="home-graph-line risk" points={riskPoints} />
        {points.map((item, index) => { const [articleX, articleY] = point(item.article_count ?? 0, articleMax, index).split(","); const [riskX, riskY] = point((item.risk_probability ?? 0) * 100, 100, index).split(","); return <g key={item.id ?? item.window_start}><circle className="home-graph-point collection" cx={articleX} cy={articleY} r="2.4"><title>{`${formatWindowDate(item.window_start)} · 수집량 ${item.article_count ?? 0}건`}</title></circle><circle className="home-graph-point risk" cx={riskX} cy={riskY} r="2.4"><title>{`${formatWindowDate(item.window_start)} · 위험도 ${formatPercent(item.risk_probability)}`}</title></circle></g>; })}
        {tickIndexes.map((index) => <text className="home-graph-date" key={index} x={STATISTICS_GRAPH_PADDING.left + (points.length < 2 ? plotWidth / 2 : index / (points.length - 1) * plotWidth)} y={STATISTICS_GRAPH_HEIGHT - 5} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}>{formatWindowDate(points[index].window_start)}</text>)}
      </svg> : <p>표시할 추세 데이터가 없습니다.</p>}
    </figure>
  </section>;
}

// 최신 15분 특징 창과 수집 완전성, 공통 모델 상태를 요약한다.
function FeatureWindowSummary({ window: featureWindow }) {
  if (!featureWindow) return <p className="panel-empty">아직 생성된 15분 특징 구간이 없습니다.</p>;
  const endTime = new Date(featureWindow.window_end).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }).replace(/^(오전|오후)\s*/, "");
  return <div className="feature-window-summary">
    <div className="feature-window-head"><div><span className="eyebrow">최근 15분 수집</span><strong>{formatDate(featureWindow.window_start)} – {endTime}</strong></div><div><span className={`quality-pill ${featureWindow.data_quality}`}>{DATA_QUALITY_LABELS[featureWindow.data_quality]}</span></div></div>
    <div className="window-metrics"><div><span>기사</span><strong>{formatNumber(featureWindow.article_count)}</strong></div><div><span>스토리</span><strong>{formatNumber(featureWindow.story_count)}</strong></div><div><span>확산</span><strong>{formatNumber(featureWindow.amplification_count)}</strong></div><div><span>언론사</span><strong>{formatNumber(featureWindow.publisher_count)}</strong></div><div><span>위험도</span><strong>{formatRiskProbability(featureWindow.risk_probability)}</strong></div></div>
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
  // v3(schema_version 3)는 v2와 겹치는 키가 하나도 없다. 아래 v2 렌더링을 그대로 두고
  // 앞에서 갈라야, 라우터가 아직 v2를 부르는 동안 화면이 바뀌지 않는다.
  if (draft.schema_version === 3 && draft.generation_kind !== "competitor_impact") {
    return <MainResponseContent content={content} />;
  }
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const isCompetitorImpact = draft.generation_kind === "competitor_impact";
  return <div className="response-draft">
    <div className="response-draft-head"><div><span className="eyebrow">RESPONSE DRAFT · REVIEW REQUIRED</span><strong>{content.risk_summary}</strong></div><span className={`draft-kind ${isCompetitorImpact ? "competitor" : "main"}`}>{isCompetitorImpact ? "경쟁사 → 나의 기업 영향" : "나의 기업 직접 대응"}</span></div>
    {scenarios.length ? <div className="response-scenario-list">{scenarios.map((scenario, index) => <article className="response-scenario" key={`${scenario.title ?? "scenario"}-${index}`}>
      <header><span>경우 {String(index + 1).padStart(2, "0")}</span><h4>{scenario.title || `${index + 1}번째 대응안`}</h4></header>
      {scenario.assumption && <p><strong>전제</strong>{scenario.assumption}</p>}
      {scenario.possible_impact && <p><strong>나의 기업 예상 영향</strong>{scenario.possible_impact}</p>}
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
    <div className="risk-detail-head"><div><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><h3>{risk.summary || risk.article_title || `위험 이벤트 #${risk.id}`}</h3></div></div>
    <p>위험도 {formatRiskProbability(risk.risk_probability)} · 이상 점수 {formatScore(risk.anomaly_score)} · {formatDate(risk.detected_at)}</p>
    <div className="risk-type-list">{risk.risk_types.map((item) => <span key={item.risk_type}>{RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type} {formatPercent(item.probability)}</span>)}</div>
    <div className="evidence-list"><strong>근거 기사</strong>{risk.evidence_articles.length ? risk.evidence_articles.map((article) => <a key={article.article_id} href={article.url} target="_blank" rel="noreferrer">{article.title}</a>) : <small>연결된 근거 기사가 없습니다.</small>}</div>
    {!latest && <button className="secondary-button" type="button" onClick={generate} disabled={loading || !risk.evidence_articles.length}>{loading ? "생성 중..." : "근거 기반 대응 초안 생성"}</button>}
    {error && <div className="notice error">{error}</div>}
    {content && <><ResponseDraftContent draft={latest} />{canReview ? <div className="draft-review"><input value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="검토 메모 (선택)" /><button type="button" onClick={() => review("approve")} disabled={loading || latest.approval_state !== "draft"}>승인</button><button type="button" onClick={() => review("reject")} disabled={loading || latest.approval_state !== "draft"}>반려</button><span>{latest.approval_state === "draft" ? "외부 전송·실행 금지" : latest.approval_state === "approved" ? `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}승인 완료` : `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}반려됨`}</span></div> : <div className="draft-review readonly"><span>{latest.approval_state === "draft" ? "멤버 승인 대기" : latest.approval_state === "approved" ? "승인 완료" : "반려됨"}</span></div>}</>}
  </div>;
}

// 기업별 실시간 수집 현황, 기사, 위험 이벤트와 제어 기능을 제공한다.
export default function AnalysisStatisticsPage({ initialCompanyId, initialRiskEventId = null, canAdminister = false }) {
  const [companies, setCompanies] = useState([]); const [selectedId, setSelectedId] = useState(initialCompanyId ? String(initialCompanyId) : "");
  const [data, setData] = useState(null); const [error, setError] = useState(null);
  const [articlePage, setArticlePage] = useState(1);
  const [riskPage, setRiskPage] = useState(1);
  const [displayMode, setDisplayMode] = useState("");
  const [articleSources, setArticleSources] = useState([]);
  const [changingState, setChangingState] = useState(false);
  const [selectedRiskId, setSelectedRiskId] = useState(initialRiskEventId);
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
      const mainCompany = nextCompanies.find((company) => company.company_role === "main");
      const id = requestedCompany?.id ?? mainCompany?.id ?? nextCompanies[0]?.id;
      if (!id) { setData(null); return; }
      if (String(id) !== String(selectedId)) {
        setSelectedId(String(id));
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
      const [monitoring, articles, risks, filtering, windows, dailySummaries] = await Promise.all([
        api.get(`/companies/${id}/monitoring`), articleRequest, api.get(`/companies/${id}/risk-events?limit=200`),
        api.get(`/companies/${id}/filter-summary`), api.get(`/companies/${id}/feature-windows?limit=672`),
        api.get(`/companies/${id}/daily-summaries?days=7`),
      ]);
      if (requestId !== refreshSequence.current) return;
      if (!filterDecision) setArticleSources(articles.data.sources);
      setData({ monitoring: monitoring.data, articles: articles.data, articleKind: filterDecision ? "filter_results" : "articles", articleDecision: filterDecision, risks: risks.data, filtering: filtering.data, windows: windows.data, dailySummaries: dailySummaries.data }); setError(null);
    } catch (requestError) { if (requestId === refreshSequence.current) setError(getErrorMessage(requestError)); }
  }, [selectedId, articlePage, displayMode]);
  // 수집 주기보다 빠른 30초 간격으로 서버 현황을 다시 조회한다.
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 30000); return () => { window.clearInterval(timer); refreshSequence.current += 1; }; }, [refresh]);
  // 서버 요청 없이 카운트다운 표시만 매초 다시 계산하도록 현재 시각을 갱신한다.
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const selected = companies.find((company) => String(company.id) === String(selectedId));
  const mainCompanies = companies.filter((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");
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
  const dailyTrendPoints = buildDailyTrendPoints(data?.dailySummaries, data?.windows);
  const secondsUntilCollection = data?.monitoring.monitoring_status === "paused"
    ? data.monitoring.collection_interval_seconds
    : data?.monitoring.next_collection_at
      ? (new Date(data.monitoring.next_collection_at).getTime() - now) / 1000
      : null;
  // 백엔드 상태와 진행 중인 요청을 조합해 제어 버튼의 동작·표시 상태를 계산한다.
  const monitoringStatus = selected?.monitoring_status;
  const monitoringActionAvailable = ["backfilling", "warming", "paused", "active"].includes(monitoringStatus);
  const showCollectionCountdown = monitoringActionAvailable
    && Boolean(data?.monitoring.next_collection_at);
  const monitorControlClass = monitoringStatus === "paused" ? "start" : "stop";
  const monitorControlLabel = changingState
      ? "처리 중..."
      : monitoringStatus === "paused"
        ? "실시간 모니터링 시작"
        : monitoringActionAvailable
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
  return <section className="workspace analysis-statistics-workspace"><div className="workspace-head"><div><p>기업별 수집량과 위험도 추세, 위험 근거와 대응 초안을 한곳에서 확인합니다.</p></div></div>
    <div className="monitor-toolbar"><label><span className="analysis-field-label">분석 기업</span><select value={selectedId} onChange={(event) => { const nextCompanyId = event.target.value; setSelectedId(nextCompanyId); setArticlePage(1); setRiskPage(1); setSelectedRiskId(null); setDisplayMode(""); setArticleSources([]); }}>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="경쟁사">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></label>{showCollectionCountdown && <div className="collection-countdown"><span>다음 기사 수집까지</span><strong>{formatCountdown(secondsUntilCollection)}</strong><small>15분 주기</small></div>}{selected && <><span className={`state-badge ${selected.monitoring_status}`}>{MONITORING_LABELS[selected.monitoring_status]}</span>{canAdminister && <button className={`monitor-control ${monitorControlClass}`} onClick={changeMonitoringState} disabled={changingState || !monitoringActionAvailable}>{monitorControlLabel}</button>}</>}</div>
    {error && <div className="notice error">{error}</div>}
    {!selected ? <p className="empty-state">먼저 기업 등록 페이지에서 모니터링할 기업을 등록해 주세요.</p> : data && <>
      <DetailTrendGraph windows={dailyTrendPoints} label={selected.name} />
      <FeatureWindowSummary window={latestWindow} />
      <div className="live-grid"><section className="panel span-two"><PanelTitle kicker={showingFilterResults ? "FILTER RESULTS" : "COLLECTED ARTICLES"} title={articlePanelTitle} /><label className="source-filter"><span className="analysis-field-label">표시할 데이터</span><select value={displayMode} onChange={(event) => { setDisplayMode(event.target.value); setArticlePage(1); }}><option value="">정제 통과 기사 전체</option><optgroup label="필터 판정"><option value={FILTERED_DATA_MODE}>필터 제외 데이터 · {formatNumber(data.filtering.rejected_count)}건</option><option value={REVIEW_DATA_MODE}>검토 필요 데이터 · {formatNumber(data.filtering.review_required_count)}건</option></optgroup><optgroup label="정제 통과 기사 출처">{articleSources.map((source) => <option value={source} key={source}>{SOURCE_LABELS[source] ?? source}</option>)}{SUPPORTED_SOURCES.filter((source) => !articleSources.includes(source)).map((source) => <option value={source} key={source} disabled>{SOURCE_LABELS[source]} · 수집 데이터 없음</option>)}</optgroup></select></label><div className="article-list">{data.articles.items.length ? (showingFilterResults ? data.articles.items.map((result) => <FilterResultRow result={result} key={result.id} />) : data.articles.items.map((article) => <a className="article-row" key={article.id} href={article.url} target="_blank" rel="noreferrer"><span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span><div><strong>{article.title}</strong><small>{SOURCE_LABELS[article.source] ?? article.source} · {formatDate(article.published_at ?? article.created_at)}</small></div></a>)) : <p className="panel-empty">{articleEmptyText}</p>}</div><Pagination page={data.articles.page} pageSize={data.articles.page_size} total={data.articles.total} onChange={setArticlePage} /></section>
        <section className="panel"><PanelTitle kicker="RISK EVENTS" title="기업 위험 이벤트" /><div className="risk-list selectable">{visibleRisks.length ? visibleRisks.map((risk) => <button className={selectedRisk?.id === risk.id ? "selected" : ""} type="button" onClick={() => setSelectedRiskId(risk.id)} key={risk.id}><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><strong>{risk.summary || risk.article_title || `위험 이벤트 #${risk.id}`}</strong><small>위험도 {formatRiskProbability(risk.risk_probability)} · {formatDate(risk.detected_at)}</small></button>) : <p className="panel-empty">새 위험 이벤트가 없습니다.</p>}</div><Pagination page={riskPage} pageSize={riskPageSize} total={data.risks.length} onChange={setRiskPage} /></section>
      </div>
      {selectedRisk && <section className="panel risk-detail-panel" ref={riskDetailRef} tabIndex={-1}><PanelTitle kicker="EVIDENCE & RESPONSE" title="위험 근거와 대응 초안" /><RiskDetail risk={selectedRisk} canReview={canAdminister} /></section>}
    </>}
  </section>;
}
