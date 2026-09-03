import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { Pagination, PanelTitle } from "../../shared/components";
import {
  DATA_QUALITY_LABELS,
  FILTER_REASON_LABELS,
  RISK_TYPE_LABELS,
  SOURCE_LABELS,
  formatDate,
  formatNumber,
  formatPercent,
  formatRiskProbability,
  formatScore,
  riskEventTitle,
  sentimentKind,
  sentimentText,
} from "../../shared/presentation";
import { RiskEventListContent } from "./AnalysisStatisticsPage";

const STAGES = [
  { id: "collection", step: "01", label: "15분 수집", kicker: "COLLECTION WINDOWS", description: "15분 단위 수집 품질과 처리량, 최근 실행 이력을 확인합니다." },
  { id: "filtering", step: "02", label: "정제", kicker: "ARTICLE FILTERING", description: "수집 원문의 관련성·광고성·중복 판정과 보류 결과를 확인합니다." },
  { id: "stories", step: "03", label: "스토리 군집화", kicker: "STORY CLUSTERING", description: "여러 출처의 유사 기사가 어떤 하나의 스토리로 묶였는지 확인합니다." },
  { id: "sentiment", step: "04", label: "감성분석", kicker: "SENTIMENT ANALYSIS", description: "정제 기사별 긍정·중립·부정 판정과 기간 분포를 확인합니다." },
  { id: "risk", step: "05", label: "위험판정", kicker: "RISK DETECTION", description: "스토리별 위험도와 유형, 사건 발생 근거를 확인합니다." },
];
const STAGE_IDS = new Set(STAGES.map((stage) => stage.id));
const PAGE_SIZE = 12;

const safeNumber = (value) => Math.max(Number(value) || 0, 0);
function Stat({ label, value, note, tone = "" }) {
  return <article className={`pipeline-stat ${tone}`}><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</article>;
}

function CollectionStage({ data }) {
  const latest = data.windows?.[0];
  return <div className="pipeline-stage-content">
    <div className="pipeline-stat-grid">
      <Stat label="최근 구간 기사" value={`${formatNumber(latest?.article_count)}건`} note="정제 통과" />
      <Stat label="최근 구간 스토리" value={`${formatNumber(latest?.story_count)}건`} note="중복 보도 통합" />
      <Stat label="출처" value={`${formatNumber(latest?.publisher_count)}곳`} note="최근 15분" />
      <Stat label="수집 품질" value={latest ? DATA_QUALITY_LABELS[latest.data_quality] : "대기"} note={latest ? formatDate(latest.window_end) : "생성 전"} tone={latest?.data_quality ?? ""} />
    </div>
    <section className="panel pipeline-panel">
      <PanelTitle kicker="15-MINUTE WINDOWS" title="최근 수집 구간" />
      <div className="pipeline-table-wrap"><table className="pipeline-table"><thead><tr><th>구간</th><th>품질</th><th>기사</th><th>스토리</th><th>확산</th><th>출처</th><th>위험도</th></tr></thead><tbody>{(data.windows ?? []).map((window) => <tr key={window.id}><td>{formatDate(window.window_start)}</td><td><span className={`quality-pill ${window.data_quality}`}>{DATA_QUALITY_LABELS[window.data_quality]}</span></td><td>{formatNumber(window.article_count)}</td><td>{formatNumber(window.story_count)}</td><td>{formatNumber(window.amplification_count)}</td><td>{formatNumber(window.publisher_count)}</td><td>{formatRiskProbability(window.risk_probability)}</td></tr>)}</tbody></table></div>
      {!data.windows?.length && <p className="panel-empty">아직 생성된 15분 수집 구간이 없습니다.</p>}
    </section>
    <section className="panel pipeline-panel">
      <PanelTitle kicker="COLLECTION JOBS" title="최근 수집 실행 이력" />
      <div className="pipeline-result-list">{(data.jobs?.items ?? []).map((job) => <article className="pipeline-result-row" key={job.id}><div><span className={`pipeline-status ${job.status}`}>{job.status === "completed" ? "완료" : job.status === "partial" ? "부분 완료" : job.status === "failed" ? "실패" : "진행 중"}</span><strong>{job.job_type === "realtime" ? "실시간 수집" : job.job_type === "backfill" ? "과거 기사 수집" : "수동 수집"}</strong></div><p>조회 {formatNumber(job.query_count)}회 · 수집 {formatNumber(job.fetched_count)}건 · 신규 {formatNumber(job.new_count)}건 · 연결 {formatNumber(job.matched_count)}건</p><small>{(job.sources ?? []).map((source) => SOURCE_LABELS[source] ?? source).join(", ")} · {formatDate(job.completed_at ?? job.started_at)}</small></article>)}</div>
      {!data.jobs?.items?.length && <p className="panel-empty">수집 실행 이력이 없습니다.</p>}
    </section>
  </div>;
}

function FilteringStage({ data, filterDecision, onDecisionChange, page, onPageChange }) {
  const summary = data.filterSummary ?? {};
  return <div className="pipeline-stage-content">
    <div className="pipeline-stat-grid">
      <Stat label="전체 판정" value={`${formatNumber(summary.raw_count)}건`} note="최신 필터 버전" />
      <Stat label="정제 통과" value={`${formatNumber(summary.accepted_count)}건`} note="다음 단계 전달" tone="success" />
      <Stat label="제외" value={`${formatNumber(summary.rejected_count)}건`} note={`중복 ${formatNumber(summary.duplicate_count)}건`} />
      <Stat label="검토 필요" value={`${formatNumber(summary.review_required_count)}건`} note="관련성·광고성 모호" tone="warning" />
    </div>
    <section className="panel pipeline-panel">
      <div className="pipeline-panel-heading"><PanelTitle kicker="FILTER RESULTS" title="정제 판정 결과" /><label><span>판정</span><select value={filterDecision} onChange={(event) => onDecisionChange(event.target.value)}><option value="">전체</option><option value="accepted">통과</option><option value="rejected">제외</option><option value="review_required">검토 필요</option></select></label></div>
      <div className="pipeline-result-list">{(data.filterResults?.items ?? []).map((item) => <a className="pipeline-result-row linked" href={item.url} target="_blank" rel="noreferrer" key={item.id}><div><span className={`filter-pill ${item.decision}`}>{item.decision === "accepted" ? "통과" : item.decision === "review_required" ? "검토 필요" : "제외"}</span><strong>{item.title}</strong></div><p>관련성 {formatScore(item.relevance_score)} · 광고성 {formatScore(item.advertising_score)} · 신뢰도 {formatScore(item.confidence)}</p><small>{FILTER_REASON_LABELS[item.reason] ?? item.reason} · {SOURCE_LABELS[item.source] ?? item.source} · {formatDate(item.filtered_at)}</small></a>)}</div>
      {!data.filterResults?.items?.length && <p className="panel-empty">선택한 조건의 정제 결과가 없습니다.</p>}
      <Pagination page={page} pageSize={PAGE_SIZE} total={data.filterResults?.total ?? 0} onChange={onPageChange} />
    </section>
  </div>;
}

function StoryStage({ data }) {
  const groups = useMemo(() => {
    const grouped = new Map();
    (data.articles?.items ?? []).forEach((article) => {
      const key = article.story_cluster_id ?? `article-${article.id}`;
      const current = grouped.get(key) ?? { id: key, articles: [], sources: new Set(), latest: null };
      current.articles.push(article);
      current.sources.add(SOURCE_LABELS[article.source] ?? article.source);
      const time = article.published_at ?? article.created_at;
      if (!current.latest || new Date(time) > new Date(current.latest)) current.latest = time;
      grouped.set(key, current);
    });
    return [...grouped.values()].sort((left, right) => new Date(right.latest) - new Date(left.latest));
  }, [data.articles?.items]);
  const totalArticles = (data.articles?.items ?? []).length;
  const amplification = Math.max(totalArticles - groups.length, 0);
  return <div className="pipeline-stage-content">
    <div className="pipeline-stat-grid">
      <Stat label="표시 스토리" value={`${formatNumber(groups.length)}건`} note="최근 기사 기준" />
      <Stat label="포함 기사" value={`${formatNumber(totalArticles)}건`} note={`전체 ${formatNumber(data.articles?.total)}건 중`} />
      <Stat label="통합 보도" value={`${formatNumber(amplification)}건`} note="스토리 내 추가 기사" />
      <Stat label="평균 기사" value={`${groups.length ? (totalArticles / groups.length).toFixed(1) : "0"}건`} note="스토리당" />
    </div>
    <section className="panel pipeline-panel">
      <PanelTitle kicker="STORY GROUPS" title="최근 스토리 군집" />
      <div className="story-cluster-list">{groups.map((group) => <article key={group.id}><header><span>STORY #{String(group.id).replace("article-", "")}</span><strong>{group.articles[0]?.title}</strong></header><div>{group.articles.map((article) => <a href={article.url} target="_blank" rel="noreferrer" key={article.id}><span>{SOURCE_LABELS[article.source] ?? article.source}</span><p>{article.title}</p><small>{formatDate(article.published_at ?? article.created_at)}</small></a>)}</div><footer>기사 {formatNumber(group.articles.length)}건 · 출처 {formatNumber(group.sources.size)}곳 · 최근 {formatDate(group.latest)}</footer></article>)}</div>
      {!groups.length && <p className="panel-empty">아직 생성된 스토리 군집이 없습니다.</p>}
    </section>
  </div>;
}

function SentimentStage({ data }) {
  const articles = data.articles?.items ?? [];
  const counts = articles.reduce((result, article) => { const kind = sentimentKind(article.sentiment_label); result[kind] = (result[kind] ?? 0) + 1; return result; }, { positive: 0, neutral: 0, negative: 0, pending: 0 });
  const analyzed = counts.positive + counts.neutral + counts.negative;
  return <div className="pipeline-stage-content">
    <div className="pipeline-stat-grid"><Stat label="분석 완료" value={`${formatNumber(analyzed)}건`} note={`표시 기사 ${formatNumber(articles.length)}건`} /><Stat label="긍정" value={`${formatNumber(counts.positive)}건`} note={formatPercent(analyzed ? counts.positive / analyzed : null)} tone="success" /><Stat label="중립" value={`${formatNumber(counts.neutral)}건`} note={formatPercent(analyzed ? counts.neutral / analyzed : null)} /><Stat label="부정" value={`${formatNumber(counts.negative)}건`} note={formatPercent(analyzed ? counts.negative / analyzed : null)} tone="danger" /></div>
    <section className="panel pipeline-panel"><PanelTitle kicker="SENTIMENT COMPOSITION" title="최근 기사 감성 분포" /><div className="sentiment-distribution">{["positive", "neutral", "negative"].map((kind) => <div key={kind}><span>{sentimentText(kind)}</span><i><b className={kind} style={{ width: `${analyzed ? counts[kind] / analyzed * 100 : 0}%` }} /></i><strong>{formatNumber(counts[kind])}건</strong></div>)}</div></section>
    <section className="panel pipeline-panel"><PanelTitle kicker="ANALYZED ARTICLES" title="기사별 감성 결과" /><div className="pipeline-result-list">{articles.map((article) => <a className="pipeline-result-row linked" href={article.url} target="_blank" rel="noreferrer" key={article.id}><div><span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span><strong>{article.title}</strong></div><p>긍정 {formatPercent(article.positive_probability)} · 중립 {formatPercent(article.neutral_probability)} · 부정 {formatPercent(article.negative_probability)}</p><small>{SOURCE_LABELS[article.source] ?? article.source} · 신뢰도 {formatPercent(article.sentiment_confidence)} · {formatDate(article.published_at ?? article.created_at)}</small></a>)}</div>{!articles.length && <p className="panel-empty">감성 분석 결과가 없습니다.</p>}</section>
  </div>;
}

function RiskStage({ data, selectedRiskId, onSelect }) {
  const events = data.risks?.items ?? [];
  const selected = events.find((risk) => risk.id === selectedRiskId) ?? events[0] ?? null;
  return <div className="pipeline-stage-content">
    <div className="pipeline-stat-grid"><Stat label="활성 사건" value={`${formatNumber(data.risks?.summary?.active)}건`} note="현재 추적 중" /><Stat label="긴급" value={`${formatNumber(data.risks?.summary?.critical)}건`} note="즉시 확인" tone="danger" /><Stat label="대응 필요" value={`${formatNumber(data.risks?.summary?.needs_response)}건`} note="보류·실패" tone="warning" /><Stat label="종료 이력" value={`${formatNumber(data.risks?.summary?.history)}건`} note="누적" /></div>
    <div className="pipeline-split"><section className="panel pipeline-panel"><PanelTitle kicker="RISK EVENTS" title="위험 판정 결과" /><div className="risk-list selectable">{events.map((risk) => <button className={`risk-event-list-item ${selected?.id === risk.id ? "selected" : ""}`} type="button" onClick={() => onSelect(risk.id)} key={risk.id}><RiskEventListContent risk={risk} /></button>)}</div>{!events.length && <p className="panel-empty">활성 위험 사건이 없습니다.</p>}</section><section className="panel pipeline-panel pipeline-risk-evidence"><PanelTitle kicker="DETECTION DETAIL" title="판정 상세와 근거" />{selected ? <><div className="pipeline-risk-head"><div><span className={`severity ${selected.severity}`}>{selected.severity === "critical" ? "긴급" : "주의"}</span><h3>{riskEventTitle(selected)}</h3></div><strong>{formatRiskProbability(selected.risk_probability)}</strong></div><div className="risk-type-list">{(selected.risk_types ?? []).map((type) => <span className={type.is_primary ? "primary" : ""} key={type.risk_type}>{RISK_TYPE_LABELS[type.risk_type] ?? type.risk_type} {formatPercent(type.probability)}</span>)}</div><div className="pipeline-evidence-list">{(selected.evidence_articles ?? []).map((article) => <a href={article.url} target="_blank" rel="noreferrer" key={article.article_id}><span>{article.evidence_role === "trigger" ? "위험 근거" : "관련 보도"}</span><strong>{article.title}</strong><small>{article.source_domain || article.source || "출처 미상"} · 근거 점수 {formatPercent(article.evidence_score)}</small></a>)}</div></> : <p className="panel-empty">확인할 위험 사건을 선택해 주세요.</p>}</section></div>
  </div>;
}

export default function AnalysisPipelinePage() {
  const { stage: requestedStage } = useParams();
  const stageId = STAGE_IDS.has(requestedStage) ? requestedStage : "collection";
  const stage = STAGES.find((item) => item.id === stageId);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [companies, setCompanies] = useState([]);
  const [data, setData] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filterDecision, setFilterDecision] = useState("");
  const [page, setPage] = useState(1);
  const requestSequence = useRef(0);
  const selectedCompanyId = searchParams.get("companyId") ?? "";
  const selectedRiskId = Number(searchParams.get("eventId")) || null;

  useEffect(() => {
    let active = true;
    api.get("/companies").then((response) => {
      if (!active) return;
      const next = response.data ?? [];
      setCompanies(next);
      const requested = next.find((company) => String(company.id) === selectedCompanyId);
      const fallback = next.find((company) => company.company_role === "main") ?? next[0];
      if (!requested && fallback) setSearchParams((current) => { const params = new URLSearchParams(current); params.set("companyId", String(fallback.id)); return params; }, { replace: true });
    }).catch((requestError) => active && setError(getErrorMessage(requestError)));
    return () => { active = false; };
  }, [selectedCompanyId, setSearchParams]);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!selectedCompanyId) { setLoading(false); return; }
    const requestId = ++requestSequence.current;
    if (!silent) setLoading(true);
    try {
      let result;
      if (stageId === "collection") {
        const [monitoring, windows, jobs] = await Promise.all([api.get(`/companies/${selectedCompanyId}/monitoring`), api.get(`/companies/${selectedCompanyId}/feature-windows?limit=24`), api.get(`/companies/${selectedCompanyId}/collection-jobs?page=1&page_size=10`)]);
        result = { monitoring: monitoring.data, windows: windows.data, jobs: jobs.data };
      } else if (stageId === "filtering") {
        const decision = filterDecision ? `&decision=${filterDecision}` : "";
        const [summary, results] = await Promise.all([api.get(`/companies/${selectedCompanyId}/filter-summary`), api.get(`/companies/${selectedCompanyId}/filter-results?page=${page}&page_size=${PAGE_SIZE}${decision}`)]);
        result = { filterSummary: summary.data, filterResults: results.data };
      } else if (["stories", "sentiment"].includes(stageId)) {
        const size = stageId === "stories" ? 100 : 50;
        const [articles, daily] = await Promise.all([api.get(`/companies/${selectedCompanyId}/articles?page=1&page_size=${size}&days=7`), api.get(`/companies/${selectedCompanyId}/daily-summaries?days=7`)]);
        result = { articles: articles.data, daily: daily.data };
      } else {
        const risks = await api.get(`/companies/${selectedCompanyId}/risk-events/page?view=active&page=1&page_size=100&response=all`);
        result = { risks: risks.data };
      }
      if (requestId !== requestSequence.current) return;
      setData(result); setError(null);
    } catch (requestError) { if (requestId === requestSequence.current) setError(getErrorMessage(requestError)); }
    finally { if (requestId === requestSequence.current) setLoading(false); }
  }, [filterDecision, page, selectedCompanyId, stageId]);

  useEffect(() => { load(); const timer = window.setInterval(() => load({ silent: true }), 30000); return () => { window.clearInterval(timer); requestSequence.current += 1; }; }, [load]);
  useEffect(() => { setPage(1); setData({}); }, [selectedCompanyId, stageId]);

  const updateRiskSelection = (eventId) => setSearchParams((current) => { const params = new URLSearchParams(current); if (eventId) params.set("eventId", String(eventId)); else params.delete("eventId"); return params; });
  const moveStage = (nextStage) => { const query = selectedCompanyId ? `?companyId=${encodeURIComponent(selectedCompanyId)}` : ""; navigate(`/analysis/${nextStage}${query}`); };
  const mainCompanies = companies.filter((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");

  return <section className="analysis-pipeline-shell">
    <aside className="analysis-pipeline-sidebar"><div><span className="eyebrow">ANALYSIS PIPELINE</span><h2>분석 과정</h2><p>수집부터 위험판정까지 단계별 결과를 확인합니다.</p></div><nav aria-label="분석 과정">{STAGES.map((item, index) => <button type="button" className={stageId === item.id ? "active" : ""} aria-current={stageId === item.id ? "page" : undefined} onClick={() => moveStage(item.id)} key={item.id}><span>{item.step}</span><strong>{item.label}</strong>{index < STAGES.length - 1 && <i aria-hidden="true" />}</button>)}</nav></aside>
    <main className="workspace analysis-statistics-workspace analysis-pipeline-workspace">
      <header className="pipeline-heading"><div><span className="eyebrow">{stage.kicker} · STEP {stage.step}</span><h1>{stage.label}</h1><p>{stage.description}</p></div><label><span>분석 기업</span><select value={selectedCompanyId} onChange={(event) => setSearchParams({ companyId: event.target.value })}><option value="" disabled>기업을 선택하세요</option>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="경쟁사">{competitorCompanies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}</optgroup>}</select></label></header>
      {error && <div className="notice error">{error}</div>}
      {loading && !Object.keys(data).length ? <p className="empty-state">{stage.label} 데이터를 불러오는 중입니다.</p> : !companies.length ? <p className="empty-state">먼저 기업을 등록해 주세요.</p> : <>
        {stageId === "collection" && <CollectionStage data={data} />}
        {stageId === "filtering" && <FilteringStage data={data} filterDecision={filterDecision} onDecisionChange={(value) => { setFilterDecision(value); setPage(1); }} page={page} onPageChange={setPage} />}
        {stageId === "stories" && <StoryStage data={data} />}
        {stageId === "sentiment" && <SentimentStage data={data} />}
        {stageId === "risk" && <RiskStage data={data} selectedRiskId={selectedRiskId} onSelect={updateRiskSelection} />}
      </>}
    </main>
  </section>;
}
