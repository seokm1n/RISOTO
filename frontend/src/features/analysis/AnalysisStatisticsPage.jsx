import { useCallback, useEffect, useRef, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { Pagination, PanelTitle, useAppConfirm } from "../../shared/components";
import RiskOverviewTrendChart from "../../shared/RiskOverviewTrendChart";
import MainResponseContent from "./MainResponseContent";
import PeerRecommendationContent from "./PeerRecommendationContent";
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

const STATISTICS_PERIOD_DAYS = 7;
const STATISTICS_PERIOD_LABEL = "최근 7일";

// 최신 15분 특징 창과 수집 완전성, 공통 모델 상태를 요약한다.
function FeatureWindowSummary({ window: featureWindow }) {
  if (!featureWindow) return <p className="panel-empty">아직 생성된 15분 특징 구간이 없습니다.</p>;
  const endTime = new Date(featureWindow.window_end).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }).replace(/^(오전|오후)\s*/, "");
  return <div className="feature-window-summary">
    <div className="feature-window-head"><div><span className="eyebrow">LATEST 15-MINUTE COLLECTION</span><h2>최근 15분 수집</h2><strong>{formatDate(featureWindow.window_start)} – {endTime}</strong></div><div><span className={`quality-pill ${featureWindow.data_quality}`}>{DATA_QUALITY_LABELS[featureWindow.data_quality]}</span></div></div>
    <div className="window-metrics"><div><span>기사</span><strong>{formatNumber(featureWindow.article_count)}<small className="count-unit">건</small></strong></div><div><span>스토리</span><strong>{formatNumber(featureWindow.story_count)}<small className="count-unit">건</small></strong></div><div><span>확산</span><strong>{formatNumber(featureWindow.amplification_count)}<small className="count-unit">건</small></strong></div><div><span>언론사</span><strong>{formatNumber(featureWindow.publisher_count)}<small className="count-unit">건</small></strong></div><div><span>위험도</span><strong>{formatRiskProbability(featureWindow.risk_probability)}</strong></div></div>
    {featureWindow.data_quality === "unavailable" && <p className="window-warning">수집 불가 구간이므로 위험도를 계산하지 않았습니다.</p>}
  </div>;
}

// 감성 분석이 끝난 기사만 분모로 삼아 날짜별 긍정·중립·부정 구성을 100% 막대로 비교한다.
function DailySentimentCompositionChart({ days }) {
  const points = [...(days ?? [])]
    .sort((left, right) => left.summary_date.localeCompare(right.summary_date))
    .map((day) => {
      const articleCount = Math.max(Number(day.article_count) || 0, 0);
      const positive = Math.max(Number(day.positive_article_count) || 0, 0);
      const neutral = Math.max(Number(day.neutral_article_count) || 0, 0);
      const negative = Math.max(Number(day.negative_article_count) || 0, 0);
      const analyzed = positive + neutral + negative;
      return {
        ...day,
        articleCount,
        positive,
        neutral,
        negative,
        analyzed,
        unclassified: Math.max(articleCount - analyzed, 0),
      };
    })
    .filter((day) => day.articleCount > 0);
  if (!points.length) return <p className="panel-empty">아직 표시할 감성 분석 데이터가 없습니다.</p>;

  const totals = points.reduce((result, day) => ({
    positive: result.positive + day.positive,
    neutral: result.neutral + day.neutral,
    negative: result.negative + day.negative,
    analyzed: result.analyzed + day.analyzed,
    unclassified: result.unclassified + day.unclassified,
  }), { positive: 0, neutral: 0, negative: 0, analyzed: 0, unclassified: 0 });
  const ratio = (count, total) => total > 0 ? count / total : null;
  const formatDay = (value) => new Date(value).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
  const columns = { gridTemplateColumns: `repeat(${points.length}, minmax(40px, 1fr))` };

  return <div className="statistics-sentiment-composition">
    <div className="statistics-sentiment-legend" aria-label="선택 기간 감성 기사 합계">
      <span className="positive"><i />긍정 <strong>{formatNumber(totals.positive)}건 · {formatPercent(ratio(totals.positive, totals.analyzed))}</strong></span>
      <span className="neutral"><i />중립 <strong>{formatNumber(totals.neutral)}건 · {formatPercent(ratio(totals.neutral, totals.analyzed))}</strong></span>
      <span className="negative"><i />부정 <strong>{formatNumber(totals.negative)}건 · {formatPercent(ratio(totals.negative, totals.analyzed))}</strong></span>
    </div>
    <div className="statistics-sentiment-chart">
      <div className="statistics-sentiment-axis" aria-hidden="true"><span>100%</span><span>50%</span><span>0%</span></div>
      <div className="statistics-sentiment-plot">
        <div className="statistics-sentiment-bars">
          <span className="statistics-sentiment-grid top" aria-hidden="true" />
          <span className="statistics-sentiment-grid middle" aria-hidden="true" />
          <span className="statistics-sentiment-grid bottom" aria-hidden="true" />
          <div className="statistics-sentiment-columns" style={columns}>
            {points.map((day) => {
              const detail = `${formatDay(day.summary_date)} · 긍정 ${day.positive}건 (${formatPercent(ratio(day.positive, day.analyzed))}) · 중립 ${day.neutral}건 (${formatPercent(ratio(day.neutral, day.analyzed))}) · 부정 ${day.negative}건 (${formatPercent(ratio(day.negative, day.analyzed))}) · 미분류·기타 ${day.unclassified}건`;
              return <div className="statistics-sentiment-day" key={day.summary_date}>
                <div className={`statistics-sentiment-stack${day.analyzed ? "" : " empty"}`} role="img" aria-label={detail} title={detail}>
                  {day.analyzed > 0 && <>
                    <span className="positive" style={{ height: `${ratio(day.positive, day.analyzed) * 100}%` }} />
                    <span className="neutral" style={{ height: `${ratio(day.neutral, day.analyzed) * 100}%` }} />
                    <span className="negative" style={{ height: `${ratio(day.negative, day.analyzed) * 100}%` }} />
                  </>}
                </div>
              </div>;
            })}
          </div>
        </div>
        <div className="statistics-sentiment-dates" style={columns}>{points.map((day) => <time dateTime={day.summary_date} key={day.summary_date}>{formatDay(day.summary_date)}</time>)}</div>
      </div>
    </div>
    <p className="statistics-sentiment-note">비율은 감성 분석 완료 기사 {formatNumber(totals.analyzed)}건 기준입니다. 미분류·기타 {formatNumber(totals.unclassified)}건은 비율에서 제외했습니다.</p>
  </div>;
}

const RESPONSE_STATUS_LABELS = {
  pending: "생성 중",
  generating: "생성 중",
  generated: "생성 완료",
  deferred: "생성 보류",
  failed: "생성 실패",
  idle: "미생성",
};

// 위험 이벤트 목록에서 스토리 제목, 다중 유형, 근거와 대응 상태를 보여준다.
export function RiskEventListContent({ risk, judgmentCompact = false }) {
  const title = riskEventTitle(risk);
  const isNonRisk = risk.classification === "non_risk";
  const types = [...(risk.risk_types ?? [])].sort((left, right) => Number(right.is_primary) - Number(left.is_primary));
  const primaryType = types.find((item) => item.is_primary) ?? types.find((item) => item.risk_type === risk.primary_type) ?? types[0];
  const secondaryTypes = types.filter((item) => item !== primaryType);
  return <>
    <strong className="risk-event-article-title risk-event-display-title"><span>{title}</span></strong>
    <div className="risk-event-type-row">{isNonRisk ? <span className="non-risk">비위험</span> : <>{primaryType ? <span className="primary">{RISK_TYPE_LABELS[primaryType.risk_type] ?? primaryType.risk_type}</span> : <span>분류 중</span>}{secondaryTypes.map((item) => <span key={item.risk_type}>{RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type}</span>)}</>}</div>
    <small className="risk-event-context">위험도 {formatRiskProbability(risk.risk_probability)}{judgmentCompact ? <> · 관련 보도 {formatNumber(risk.evidence_article_count ?? risk.evidence_articles?.length ?? 0)}건</> : <> · 위험 판정 기사 {formatNumber(risk.risk_article_count ?? 0)}건 · 관련 보도 {formatNumber(risk.evidence_article_count ?? risk.evidence_articles?.length ?? 0)}건 · 출처 {formatNumber(risk.risk_source_count ?? risk.source_count ?? 0)}곳</>}</small>
    {!judgmentCompact && <div className="risk-event-list-footer"><small>마지막 근거 {formatDate(risk.last_evidence_at ?? risk.last_seen_at ?? risk.opened_at)}</small><span className={`response-status ${risk.response_generation_status}`}>{RESPONSE_STATUS_LABELS[risk.response_generation_status] ?? "미생성"}</span></div>}
  </>;
}

const HORIZON_LABELS = { immediate: "즉시", within_24h: "24시간 이내", within_7d: "7일 이내" };

function ActionGroups({ actions }) {
  const groups = Object.entries(actions ?? {}).filter(([, items]) => items?.length > 0);
  const count = groups.reduce((total, [, items]) => total + items.length, 0);
  if (!groups.length) return null;
  let order = 0;
  return <section className="response-workboard response-legacy-workboard">
    <header className="response-section-heading"><div><span>실행 계획</span><h4>지금부터 해야 할 일</h4></div><strong>{count}개 과제</strong></header>
    <div className="response-time-groups">{groups.map(([horizon, items]) => <article className="response-time-group" key={horizon}>
      <header><strong>{HORIZON_LABELS[horizon] ?? horizon.replaceAll("_", " ")}</strong><span>{items.length}개</span></header>
      <ol>{items.map((item, index) => {
        order += 1;
        const action = typeof item === "string" ? item : item.action;
        return <li key={`${horizon}-${index}`}><span className="response-task-number">{String(order).padStart(2, "0")}</span><div className="response-task-copy"><strong>{action}</strong></div><span className="response-task-due">{HORIZON_LABELS[horizon] ?? "시점 확인"}</span></li>;
      })}</ol>
    </article>)}</div>
  </section>;
}

function LegacyResponseContent({ content, riskTitle, isCompetitorImpact }) {
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const [active, setActive] = useState(0);
  const current = scenarios[active] ?? scenarios[0];
  const overviewItems = current ? [
    ["판단 전제", current.assumption],
    ["예상 영향", current.possible_impact],
    ["영향 경로", current.transmission_path],
  ].filter(([, value]) => value) : [];

  return <div className="response-draft response-draft-v3 response-operations-view response-legacy-view">
    <section className="response-command-card standard"><div className="response-command-copy"><span className="response-ui-kicker">AI 대응 가이드</span><div className="response-command-title"><span className="response-priority-pill standard">대응안</span><h4>{riskTitle || content.risk_summary || "위험 사건 대응"}</h4></div><p>{isCompetitorImpact ? "동종 기업 이슈가 우리 기업에 미칠 영향과 준비 항목입니다." : "위험 확산을 줄이기 위한 우선 대응 항목입니다."}</p></div><dl className="response-command-facts"><div><dt>대응 대상</dt><dd>{isCompetitorImpact ? "동종 기업 영향" : "우리 기업 사건"}</dd></div></dl></section>
    {scenarios.length > 1 && <section className="response-option-panel" aria-label="대응안 선택"><header className="response-section-heading compact"><div><span>대응 방향 선택</span><h4>확인할 대응안을 선택하세요</h4></div><strong>{scenarios.length}개 안</strong></header><div className="response-option-tabs" role="tablist">{scenarios.map((scenario, index) => <button type="button" role="tab" className={`response-option-tab${active === index ? " active" : ""}`} aria-selected={active === index} onClick={() => setActive(index)} key={`${scenario.title ?? "scenario"}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><strong>{scenario.title || `${index + 1}번째 대응안`}</strong></button>)}</div></section>}
    {current && <div className="response-plan-content"><section className="response-overview-panel"><header className="response-section-heading"><div><span>상황 요약</span><h4>{current.title || "선택한 대응안"}</h4></div></header>{overviewItems.length > 0 && <div className="response-legacy-overview">{overviewItems.map(([label, value]) => <article key={label}><h5>{label}</h5><p>{value}</p></article>)}</div>}{current.early_indicators?.length > 0 && <article className="response-legacy-indicators"><h5>조기 관찰 지표</h5><div className="response-metric-chips">{current.early_indicators.map((indicator, index) => <span key={`${indicator}-${index}`}>{indicator}</span>)}</div></article>}</section><ActionGroups actions={current.recommended_actions} /></div>}
    {!current && <ActionGroups actions={content.recommended_actions} />}
    {content.uncertainty && <aside className="response-quality-alert neutral"><strong>판단 시 유의할 점</strong><p>{content.uncertainty}</p></aside>}
  </div>;
}

function ResponseDraftContent({ draft, riskTitle }) {
  const content = draft.content ?? {};
  if (draft.schema_version === 3 && draft.generation_kind !== "competitor_impact") {
    return <MainResponseContent key={draft.id} content={content} />;
  }
  if (draft.schema_version === 3 && draft.generation_kind === "competitor_impact") {
    return <PeerRecommendationContent key={draft.id} content={content} />;
  }
  return <LegacyResponseContent key={draft.id} content={content} riskTitle={riskTitle} isCompetitorImpact={draft.generation_kind === "competitor_impact"} />;
}

// 위험 이벤트의 유형과 관리 승인이 필요한 대응 초안을 표시한다.
export function RiskDetail({ risk, canReview = false, onGenerationStarted }) {
  const riskId = risk?.id ?? null;
  const [drafts, setDrafts] = useState([]); const [loading, setLoading] = useState(false);
  const [notes, setNotes] = useState(""); const [error, setError] = useState(null);
  const [generationStatus, setGenerationStatus] = useState(risk?.response_generation_status ?? "idle");
  const loadDrafts = useCallback(async () => {
    if (!riskId) { setDrafts([]); return; }
    try {
      const response = await api.get(`/risk-events/${riskId}/response-drafts`);
      const nextDrafts = response.data ?? [];
      setDrafts(nextDrafts);
    } catch (requestError) { setError(getErrorMessage(requestError)); }
  }, [riskId]);
  useEffect(() => { setDrafts([]); setNotes(""); setError(null); loadDrafts(); }, [loadDrafts]);
  useEffect(() => { setGenerationStatus(risk?.response_generation_status ?? "idle"); }, [risk?.response_generation_status, riskId]);
  useEffect(() => {
    if (!riskId || !["pending", "generating"].includes(generationStatus)) return undefined;
    const timer = window.setInterval(loadDrafts, 5000);
    return () => window.clearInterval(timer);
  }, [generationStatus, loadDrafts, riskId]);
  if (!risk) return <p className="panel-empty">확인할 위험 이벤트를 선택해 주세요.</p>;

  const v3Draft = drafts.find((draft) => draft.schema_version === 3);
  const latest = v3Draft ?? drafts[0]; const content = latest?.content;
  const canGenerate = ["idle", "pending", "generating", "deferred", "failed"].includes(generationStatus);
  const statusCopy = {
    pending: ["대응방안 자동 생성 중", "사건 내용을 바탕으로 생성을 준비하고 있습니다."],
    generating: ["대응방안 자동 생성 중", "사건을 검토해 대응방안을 작성하고 있습니다."],
    generated: ["대응방안 생성 완료", "아래에서 우선 실행 항목과 담당 부서, 기한을 확인할 수 있습니다."],
    deferred: ["대응방안 생성 보류", "전체 이력 재구성 사건입니다. 필요한 사건만 개별 생성할 수 있습니다."],
    failed: ["대응방안 생성 실패", risk.response_generation_error || "생성에 실패했습니다. 다시 시도할 수 있습니다."],
    idle: ["대응방안 없음", "이 사건에는 생성된 대응방안이 없습니다."],
  }[generationStatus] ?? ["대응방안 없음", "이 사건에는 생성된 대응방안이 없습니다."];
  const orderedTypes = [...(risk.risk_types ?? [])].sort((left, right) => Number(right.is_primary) - Number(left.is_primary));

  const generate = async () => {
    setLoading(true); setError(null);
    try {
      const force = ["pending", "generating"].includes(generationStatus) ? "?force=true" : "";
      const response = await api.post(`/risk-events/${risk.id}/response-generation${force}`);
      setGenerationStatus(response.data?.status ?? "pending");
      onGenerationStarted?.();
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  };
  const review = async (decision) => {
    if (!latest) return;
    setLoading(true); setError(null);
    try { await api.post(`/response-drafts/${latest.id}/${decision}`, { notes }); await loadDrafts(); }
    catch (requestError) { setError(getErrorMessage(requestError)); } finally { setLoading(false); }
  };
  return <div className="risk-detail">
    <div className="risk-detail-head"><div><h3><strong className="risk-event-display-title">{riskEventTitle(risk)}</strong></h3></div><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span></div>
    <div className="risk-type-list">{orderedTypes.map((item, index) => <span className={item.is_primary || index === 0 ? "primary" : ""} key={item.risk_type}>{RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type} {formatPercent(item.probability)}</span>)}</div>
    <div className={`draft-generation-toolbar ${generationStatus}`}>
      <div><strong>{statusCopy[0]}</strong><small>{statusCopy[1]}</small></div>
      {canReview && canGenerate && <button className="secondary-button" type="button" onClick={generate} disabled={loading}>{loading ? "요청 중..." : ["pending", "generating"].includes(generationStatus) ? "생성 다시 시작" : ["idle", "deferred"].includes(generationStatus) ? "대응방안 생성" : "다시 시도"}</button>}
    </div>
    {error && <div className="notice error">{error}</div>}
    {content && <><ResponseDraftContent draft={latest} riskTitle={riskEventTitle(risk)} />{content.status === "근거부족_보류" ? <div className="draft-review readonly"><span>근거 연결 후 다시 생성해 주세요.</span></div> : canReview ? <div className="draft-review"><input value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="검토 메모 (선택)" /><button type="button" onClick={() => review("approve")} disabled={loading || latest.approval_state !== "draft"}>승인</button><button type="button" onClick={() => review("reject")} disabled={loading || latest.approval_state !== "draft"}>반려</button><span>{latest.approval_state === "draft" ? "외부 전송·실행 금지" : latest.approval_state === "approved" ? `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}승인 완료` : `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}반려됨`}</span></div> : <div className="draft-review readonly"><span>{latest.approval_state === "draft" ? "멤버 승인 대기" : latest.approval_state === "approved" ? "승인 완료" : "반려됨"}</span></div>}</>}
  </div>;
}

// 기업별 실시간 수집 현황, 기사, 위험 이벤트와 제어 기능을 제공한다.
export default function AnalysisStatisticsPage({ initialCompanyId, canAdminister = false, onOpenCollectedArticles, onOpenRiskManagement, onMonitoringChanged }) {
  const [companies, setCompanies] = useState([]); const [selectedId, setSelectedId] = useState(initialCompanyId ? String(initialCompanyId) : "");
  const [data, setData] = useState(null); const [error, setError] = useState(null);
  const [changingState, setChangingState] = useState(false);
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
      const featureWindowLimit = STATISTICS_PERIOD_DAYS * 96;
      const [monitoring, windows, dailySummaries] = await Promise.all([
        api.get(`/companies/${id}/monitoring`), api.get(`/companies/${id}/feature-windows?limit=${featureWindowLimit}`),
        api.get(`/companies/${id}/daily-summaries?days=${STATISTICS_PERIOD_DAYS}`),
      ]);
      if (requestId !== refreshSequence.current) return;
      setData({ monitoring: monitoring.data, windows: windows.data, dailySummaries: dailySummaries.data }); setError(null);
    } catch (requestError) { if (requestId === refreshSequence.current) setError(getErrorMessage(requestError)); }
  }, [selectedId]);
  // 수집 주기보다 빠른 30초 간격으로 서버 현황을 다시 조회한다.
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 30000); return () => { window.clearInterval(timer); refreshSequence.current += 1; }; }, [refresh]);
  // 서버 요청 없이 카운트다운 표시만 매초 다시 계산하도록 현재 시각을 갱신한다.
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const selected = companies.find((company) => String(company.id) === String(selectedId));
  const mainCompanies = companies.filter((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");
  const latestWindow = data?.windows?.[0] ?? null;
  const periodArticleCount = data?.dailySummaries?.reduce((sum, summary) => sum + (summary.article_count ?? 0), 0) ?? 0;
  const periodRiskEventCount = data?.dailySummaries?.reduce((sum, summary) => sum + (summary.risk_event_count ?? 0), 0) ?? 0;
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
      await Promise.all([refresh(), onMonitoringChanged?.()]);
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setChangingState(false); }
  };
  const confirmPageMove = async (pageName, message, move) => {
    const confirmed = await confirm({ kicker: "PAGE NAVIGATION", title: `${pageName} 페이지로 이동합니다.`, message, confirmLabel: "이동" });
    if (confirmed) move?.();
  };
  return <section className="workspace analysis-statistics-workspace"><div className="workspace-head"><div><p>기업별 수집량과 위험·부정 기사 비율, 수집 기사와 위험 이벤트를 한곳에서 확인합니다.</p></div></div>
    <div className="monitor-toolbar"><div className="analysis-toolbar-filters"><label><span className="analysis-field-label">분석 기업</span><select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="비교 기업">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></label></div><div className="analysis-toolbar-actions">{selected && canAdminister && <button className={`monitor-control ${monitorControlClass}`} onClick={changeMonitoringState} disabled={changingState || !monitoringActionAvailable}>{monitorControlLabel}</button>}{showCollectionCountdown && <div className="collection-countdown"><span>다음 기사 수집까지</span><strong>{formatCountdown(secondsUntilCollection)}</strong><small>15분 주기</small></div>}</div></div>
    {error && <div className="notice error">{error}</div>}
    {!selected ? <p className="empty-state">먼저 기업 등록 페이지에서 모니터링할 기업을 등록해 주세요.</p> : data && <>
      <div className="statistics-count-grid"><button className="panel statistics-count-card" type="button" onClick={() => confirmPageMove("수집 현황", "선택한 기업의 최근 7일 수집 기사 목록을 팝업으로 엽니다.", () => onOpenCollectedArticles(selected.id, STATISTICS_PERIOD_DAYS))} aria-label={`${selected.name} ${STATISTICS_PERIOD_LABEL} 수집 기사 ${formatNumber(periodArticleCount)}건 보기`}><PanelTitle kicker="COLLECTED ARTICLES" title="최근 7일 수집된 기사" /><div><strong>{formatNumber(periodArticleCount)}</strong>건<span></span></div></button><button className="panel statistics-count-card risk" type="button" onClick={() => confirmPageMove("위험 관리", "선택한 기업의 위험 이벤트와 대응 초안을 확인합니다.", () => onOpenRiskManagement(selected.id, STATISTICS_PERIOD_DAYS))} aria-label={`${selected.name} ${STATISTICS_PERIOD_LABEL} 위험 이벤트 ${formatNumber(periodRiskEventCount)}건 보기`}><PanelTitle kicker="RISK EVENTS" title="최근 7일 발생한 위험 이벤트" /><div><strong>{formatNumber(periodRiskEventCount)}</strong>건<span></span></div></button></div>
      <FeatureWindowSummary window={latestWindow} />
      <section className="panel statistics-overview-trend">
        <PanelTitle kicker={`${STATISTICS_PERIOD_LABEL} · 기사 2건 이상 스토리 기준`} title="위험 판정·부정 기사 비율 추이" />
        <RiskOverviewTrendChart days={data.dailySummaries} ariaLabel={`${selected.name} ${STATISTICS_PERIOD_LABEL} 위험 판정 기사와 부정 기사 비율 추이`} />
      </section>
      <section className="panel statistics-sentiment-panel">
        <PanelTitle kicker={`${STATISTICS_PERIOD_LABEL} · 감성 분석 완료 기사 기준`} title="날짜별 긍정·중립·부정 기사 비율" />
        <DailySentimentCompositionChart days={data.dailySummaries} />
      </section>
    </>}
    {confirmationDialog}
  </section>;
}
