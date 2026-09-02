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
    });
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

// 위험 이벤트 목록에서 내부 코드 대신 대표 근거 기사와 한글 위험 유형을 보여준다.
export function RiskEventListContent({ risk }) {
  const title = riskEventTitle(risk);
  const types = (risk.risk_types ?? [])
    .map((item) => RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type)
    .join(" · ");
  return <>
    <strong className="risk-event-article-title risk-event-display-title"><span>{title}</span></strong>
    <small className="risk-event-context">위험 유형: {types || "분류 중"} · 위험도 {formatRiskProbability(risk.risk_probability)}</small>
    <small>발생 시각 {formatDate(risk.opened_at ?? risk.detected_at)}</small>
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
  // v3(schema_version 3)는 v2와 겹치는 키가 하나도 없다. 아래 v2 렌더링을 그대로 두고
  // 앞에서 갈라야, 라우터가 아직 v2를 부르는 동안 화면이 바뀌지 않는다.
  if (draft.schema_version === 3 && draft.generation_kind !== "competitor_impact") {
    return <MainResponseContent content={content} />;
  }
  // 동종 경로는 구조가 또 다르다(scenarios가 없고 impact·recommendation을 읽는다).
  // content_kind가 아니라 generation_kind로 가르는 이유: 근거부족_보류 content에는
  // content_kind가 없어서, 그 조건으로 잡으면 기사 0건인 동종 초안이 샌다.
  if (draft.schema_version === 3 && draft.generation_kind === "competitor_impact") {
    return <PeerRecommendationContent content={content} />;
  }
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
  useEffect(() => { setDrafts([]); setNotes(""); setError(null); loadDrafts(); }, [loadDrafts]);
  if (!risk) return <p className="panel-empty">확인할 위험 이벤트를 선택해 주세요.</p>;
  // 전환 전에 만들어진 v2보다 v3 초안을 우선한다. 기존 v2만 있으면 새 엔진 생성 버튼을
  // 계속 노출해 사용자가 현재 형식으로 올릴 수 있게 한다.
  const v3Draft = drafts.find((draft) => draft.schema_version === 3);
  const latest = v3Draft ?? drafts[0]; const content = latest?.content;
  const generate = async () => {
    setLoading(true); setError(null);
    try {
      await api.post(`/risk-events/${risk.id}/response-drafts${v3Draft ? "?force=true" : ""}`);
      await loadDrafts();
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
    <div className="risk-detail-head"><div><h3><strong className="risk-event-display-title">{riskEventTitle(risk)}</strong></h3></div></div>
    <p>위험도 {formatRiskProbability(risk.risk_probability)} · 이상 점수 {formatScore(risk.anomaly_score)} · 발생 {formatDate(risk.opened_at ?? risk.detected_at)}</p>
    <div className="risk-type-list">{risk.risk_types.map((item) => <span key={item.risk_type}>{RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type} {formatPercent(item.probability)}</span>)}</div>
    <div className="evidence-list"><strong>근거 기사</strong>{risk.evidence_articles.length ? risk.evidence_articles.map((article) => <a key={article.article_id} href={article.url} target="_blank" rel="noreferrer">{article.title}</a>) : <small>연결된 근거 기사가 없습니다.</small>}</div>
    <div className="draft-generation-toolbar">
      <div><strong>{v3Draft ? "대응방안 v3" : latest ? "기존 대응 초안" : "대응 초안 없음"}</strong><small>{v3Draft ? "최신 대응 엔진으로 생성됨" : latest ? "v3 대응방안을 새로 생성할 수 있습니다." : "근거를 바탕으로 실행 가능한 대응방안을 생성합니다."}</small></div>
      <button className="secondary-button" type="button" onClick={generate} disabled={loading}>{loading ? "생성 중..." : v3Draft ? "대응방안 다시 생성" : "대응방안 v3 생성"}</button>
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
    <div className="monitor-toolbar"><div className="analysis-toolbar-filters"><label><span className="analysis-field-label">분석 기업</span><select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="경쟁사">{competitorCompanies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</optgroup>}</select></label></div><div className="analysis-toolbar-actions">{selected && canAdminister && <button className={`monitor-control ${monitorControlClass}`} onClick={changeMonitoringState} disabled={changingState || !monitoringActionAvailable}>{monitorControlLabel}</button>}{showCollectionCountdown && <div className="collection-countdown"><span>다음 기사 수집까지</span><strong>{formatCountdown(secondsUntilCollection)}</strong><small>15분 주기</small></div>}</div></div>
    {error && <div className="notice error">{error}</div>}
    {!selected ? <p className="empty-state">먼저 기업 등록 페이지에서 모니터링할 기업을 등록해 주세요.</p> : data && <>
      <div className="statistics-count-grid"><button className="panel statistics-count-card" type="button" onClick={() => confirmPageMove("수집 현황", "선택한 기업의 최근 7일 수집 기사 목록을 팝업으로 엽니다.", () => onOpenCollectedArticles(selected.id, STATISTICS_PERIOD_DAYS))} aria-label={`${selected.name} ${STATISTICS_PERIOD_LABEL} 수집 기사 ${formatNumber(periodArticleCount)}건 보기`}><PanelTitle kicker="COLLECTED ARTICLES" title="최근 7일 수집된 기사" /><div><strong>{formatNumber(periodArticleCount)}</strong>건<span></span></div></button><button className="panel statistics-count-card risk" type="button" onClick={() => confirmPageMove("위험 관리", "선택한 기업의 위험 이벤트와 대응 초안을 확인합니다.", () => onOpenRiskManagement(selected.id, STATISTICS_PERIOD_DAYS))} aria-label={`${selected.name} ${STATISTICS_PERIOD_LABEL} 위험 이벤트 ${formatNumber(periodRiskEventCount)}건 보기`}><PanelTitle kicker="RISK EVENTS" title="최근 7일 발생한 위험 이벤트" /><div><strong>{formatNumber(periodRiskEventCount)}</strong>건<span></span></div></button></div>
      <FeatureWindowSummary window={latestWindow} />
      <section className="panel statistics-overview-trend">
        <PanelTitle kicker={`${STATISTICS_PERIOD_LABEL} · 왼쪽 건수 / 오른쪽 비율`} title="수집·위험·부정 기사 추이" />
        <RiskOverviewTrendChart days={data.dailySummaries} ariaLabel={`${selected.name} ${STATISTICS_PERIOD_LABEL} 수집량, 위험 수집 비율, 부정 기사 비율 추이`} />
      </section>
      <section className="panel statistics-sentiment-panel">
        <PanelTitle kicker={`${STATISTICS_PERIOD_LABEL} · 감성 분석 완료 기사 기준`} title="날짜별 긍정·중립·부정 기사 비율" />
        <DailySentimentCompositionChart days={data.dailySummaries} />
      </section>
    </>}
    {confirmationDialog}
  </section>;
}
