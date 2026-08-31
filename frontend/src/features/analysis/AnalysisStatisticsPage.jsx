import { useCallback, useEffect, useRef, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { PanelTitle, useAppConfirm } from "../../shared/components";
import {
  DATA_QUALITY_LABELS,
  RISK_TYPE_LABELS,
  formatCountdown,
  formatDate,
  formatNumber,
  formatPercent,
  formatRiskProbability,
  formatScore,
  riskEventTitle,
} from "../../shared/presentation";

const STATISTICS_GRAPH_WIDTH = 720;
const STATISTICS_GRAPH_HEIGHT = 230;
const STATISTICS_GRAPH_PADDING = { top: 18, right: 12, bottom: 24, left: 8 };
const STATISTICS_PERIOD_OPTIONS = [
  { days: 1, label: "최근 1일" },
  { days: 3, label: "최근 3일" },
  { days: 7, label: "최근 7일" },
  { days: 14, label: "최근 14일" },
];
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

// 최신 15분 특징 창과 수집 완전성, 공통 모델 상태를 요약한다.
function FeatureWindowSummary({ window: featureWindow }) {
  if (!featureWindow) return <p className="panel-empty">아직 생성된 15분 특징 구간이 없습니다.</p>;
  const endTime = new Date(featureWindow.window_end).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }).replace(/^(오전|오후)\s*/, "");
  return <div className="feature-window-summary">
    <div className="feature-window-head"><div><span className="eyebrow">LATEST 15-MINUTE COLLECTION</span><h2>최근 15분 수집</h2><strong>{formatDate(featureWindow.window_start)} – {endTime}</strong></div><div><span className={`quality-pill ${featureWindow.data_quality}`}>{DATA_QUALITY_LABELS[featureWindow.data_quality]}</span></div></div>
    <div className="window-metrics"><div><span>기사</span><strong>{formatNumber(featureWindow.article_count)}</strong></div><div><span>스토리</span><strong>{formatNumber(featureWindow.story_count)}</strong></div><div><span>확산</span><strong>{formatNumber(featureWindow.amplification_count)}</strong></div><div><span>언론사</span><strong>{formatNumber(featureWindow.publisher_count)}</strong></div><div><span>위험도</span><strong>{formatRiskProbability(featureWindow.risk_probability)}</strong></div></div>
    {featureWindow.data_quality === "unavailable" && <p className="window-warning">수집 불가 구간이므로 위험도를 계산하지 않았습니다.</p>}
  </div>;
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

// 위험 이벤트 목록에서 내부 코드 대신 대표 근거 기사와 한글 위험 유형을 보여준다.
export function RiskEventListContent({ risk }) {
  const title = riskEventTitle(risk);
  const types = (risk.risk_types ?? [])
    .map((item) => RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type)
    .join(" · ");
  return <>
    <strong className="risk-event-article-title risk-event-display-title"><span>{title}</span></strong>
    <small className="risk-event-context">위험 유형: {types || "분류 중"} · 위험도 {formatRiskProbability(risk.risk_probability)}</small>
    <small>탐지 시각 {formatDate(risk.detected_at)}</small>
  </>;
}

const HORIZON_LABELS = { immediate: "즉시", within_24h: "24시간 이내", within_7d: "7일 이내" };

function ActionGroups({ actions }) {
  return Object.entries(actions ?? {}).map(([horizon, items]) => <section className="scenario-actions" key={horizon}>
    <h5>{HORIZON_LABELS[horizon] ?? horizon}</h5>
    {(items ?? []).map((item, index) => <div className="scenario-action" key={`${horizon}-${index}`}><p>{typeof item === "string" ? item : item.action}</p>{typeof item !== "string" && item.evidence_urls?.map((url, urlIndex) => <a href={url} target="_blank" rel="noreferrer" key={url}>근거 {urlIndex + 1}</a>)}</div>)}
  </section>);
}

function ResponseDraftContent({ draft, riskTitle }) {
  const content = draft.content ?? {};
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const isCompetitorImpact = draft.generation_kind === "competitor_impact";
  return <div className="response-draft">
    <div className="response-draft-head"><div><span className="eyebrow">RESPONSE DRAFT · REVIEW REQUIRED</span><strong className="risk-event-display-title">{riskTitle || content.risk_summary}</strong></div><span className={`draft-kind ${isCompetitorImpact ? "competitor" : "main"}`}>{isCompetitorImpact ? "경쟁사 → 나의 기업 영향" : "나의 기업 직접 대응"}</span></div>
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
export function RiskDetail({ risk, canReview = false }) {
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
    <div className="risk-detail-head"><div><h3><strong className="risk-event-display-title">{riskEventTitle(risk)}</strong></h3></div></div>
    <p>위험도 {formatRiskProbability(risk.risk_probability)} · 이상 점수 {formatScore(risk.anomaly_score)} · {formatDate(risk.detected_at)}</p>
    <div className="risk-type-list">{risk.risk_types.map((item) => <span key={item.risk_type}>{RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type} {formatPercent(item.probability)}</span>)}</div>
    <div className="evidence-list"><strong>근거 기사</strong>{risk.evidence_articles.length ? risk.evidence_articles.map((article) => <a key={article.article_id} href={article.url} target="_blank" rel="noreferrer">{article.title}</a>) : <small>연결된 근거 기사가 없습니다.</small>}</div>
    {!latest && <button className="secondary-button" type="button" onClick={generate} disabled={loading || !risk.evidence_articles.length}>{loading ? "생성 중..." : "근거 기반 대응 초안 생성"}</button>}
    {error && <div className="notice error">{error}</div>}
    {content && <><ResponseDraftContent draft={latest} riskTitle={riskEventTitle(risk)} />{canReview ? <div className="draft-review"><input value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="검토 메모 (선택)" /><button type="button" onClick={() => review("approve")} disabled={loading || latest.approval_state !== "draft"}>승인</button><button type="button" onClick={() => review("reject")} disabled={loading || latest.approval_state !== "draft"}>반려</button><span>{latest.approval_state === "draft" ? "외부 전송·실행 금지" : latest.approval_state === "approved" ? `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}승인 완료` : `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}반려됨`}</span></div> : <div className="draft-review readonly"><span>{latest.approval_state === "draft" ? "멤버 승인 대기" : latest.approval_state === "approved" ? "승인 완료" : "반려됨"}</span></div>}</>}
  </div>;
}

// 기업별 실시간 수집 현황, 기사, 위험 이벤트와 제어 기능을 제공한다.
export default function AnalysisStatisticsPage({ initialCompanyId, canAdminister = false, onOpenCollectedArticles, onOpenRiskManagement }) {
  const [companies, setCompanies] = useState([]); const [selectedId, setSelectedId] = useState(initialCompanyId ? String(initialCompanyId) : "");
  const [data, setData] = useState(null); const [error, setError] = useState(null);
  const [changingState, setChangingState] = useState(false);
  const [periodDays, setPeriodDays] = useState(7);
  const [now, setNow] = useState(Date.now());
  const refreshSequence = useRef(0);
  const { confirm, confirmationDialog } = useAppConfirm();
  // 선택 기업의 모니터링 수치와 추세 데이터를 병렬로 갱신한다.
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
      const featureWindowLimit = periodDays * 96;
      const [monitoring, windows, dailySummaries] = await Promise.all([
        api.get(`/companies/${id}/monitoring`), api.get(`/companies/${id}/feature-windows?limit=${featureWindowLimit}`),
        api.get(`/companies/${id}/daily-summaries?days=${periodDays}`),
      ]);
      if (requestId !== refreshSequence.current) return;
      setData({ monitoring: monitoring.data, windows: windows.data, dailySummaries: dailySummaries.data }); setError(null);
    } catch (requestError) { if (requestId === refreshSequence.current) setError(getErrorMessage(requestError)); }
  }, [selectedId, periodDays]);
  // 수집 주기보다 빠른 30초 간격으로 서버 현황을 다시 조회한다.
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 30000); return () => { window.clearInterval(timer); refreshSequence.current += 1; }; }, [refresh]);
  // 서버 요청 없이 카운트다운 표시만 매초 다시 계산하도록 현재 시각을 갱신한다.
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const selected = companies.find((company) => String(company.id) === String(selectedId));
  const mainCompanies = companies.filter((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");
  const latestWindow = data?.windows?.[0] ?? null;
  const dailyTrendPoints = buildDailyTrendPoints(data?.dailySummaries, data?.windows);
  const periodArticleCount = data?.dailySummaries?.reduce((sum, summary) => sum + (summary.article_count ?? 0), 0) ?? 0;
  const periodRiskEventCount = data?.dailySummaries?.reduce((sum, summary) => sum + (summary.risk_event_count ?? 0), 0) ?? 0;
  const periodLabel = STATISTICS_PERIOD_OPTIONS.find((option) => option.days === periodDays)?.label ?? `최근 ${periodDays}일`;
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
  const confirmPageMove = async (pageName, message, move) => {
    const confirmed = await confirm({ kicker: "PAGE NAVIGATION", title: `${pageName} 페이지로 이동합니다.`, message, confirmLabel: "이동" });
    if (confirmed) move?.();
  };
  return <section className="workspace analysis-statistics-workspace"><div className="workspace-head"><div><p>기업별 수집량과 위험도 추세, 수집 기사와 위험 이벤트를 한곳에서 확인합니다.</p></div></div>
    <div className="monitor-toolbar"><div className="analysis-toolbar-filters"><label><span className="analysis-field-label">분석 기업</span><select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="경쟁사">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></label><label><span className="analysis-field-label">기간</span><select value={periodDays} onChange={(event) => setPeriodDays(Number(event.target.value))}>{STATISTICS_PERIOD_OPTIONS.map((option) => <option value={option.days} key={option.days}>{option.label}</option>)}</select></label></div><div className="analysis-toolbar-actions">{selected && canAdminister && <button className={`monitor-control ${monitorControlClass}`} onClick={changeMonitoringState} disabled={changingState || !monitoringActionAvailable}>{monitorControlLabel}</button>}{showCollectionCountdown && <div className="collection-countdown"><span>다음 기사 수집까지</span><strong>{formatCountdown(secondsUntilCollection)}</strong><small>15분 주기</small></div>}</div></div>
    {error && <div className="notice error">{error}</div>}
    {!selected ? <p className="empty-state">먼저 기업 등록 페이지에서 모니터링할 기업을 등록해 주세요.</p> : data && <>
      <div className="statistics-count-grid"><button className="panel statistics-count-card" type="button" onClick={() => confirmPageMove("수집 현황", "선택한 기업의 수집 기사 목록을 팝업으로 엽니다.", () => onOpenCollectedArticles(selected.id))} aria-label={`${selected.name} ${periodLabel} 수집 기사 ${formatNumber(periodArticleCount)}건 보기`}><PanelTitle kicker="COLLECTED ARTICLES" title="기간 중 수집된 기사" /><div><strong>{formatNumber(periodArticleCount)}</strong>건<span></span></div></button><button className="panel statistics-count-card risk" type="button" onClick={() => confirmPageMove("위험 관리", "선택한 기업의 위험 이벤트와 대응 초안을 확인합니다.", () => onOpenRiskManagement(selected.id, periodDays))} aria-label={`${selected.name} ${periodLabel} 위험 이벤트 ${formatNumber(periodRiskEventCount)}건 보기`}><PanelTitle kicker="RISK EVENTS" title="기간 중 발생한 위험 이벤트" /><div><strong>{formatNumber(periodRiskEventCount)}</strong>건<span></span></div></button></div>
      <FeatureWindowSummary window={latestWindow} />
      <DetailTrendGraph windows={dailyTrendPoints} label={`${selected.name} ${periodLabel}`} />
    </>}
    {confirmationDialog}
  </section>;
}
