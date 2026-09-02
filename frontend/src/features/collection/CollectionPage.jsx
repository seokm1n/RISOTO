import { useCallback, useEffect, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { Pagination, PanelTitle, useAppConfirm } from "../../shared/components";
import {
  FILTERED_DATA_MODE,
  FILTER_REASON_LABELS,
  HEALTH_STATUS_LABELS,
  MONITORING_LABELS,
  REVIEW_DATA_MODE,
  SOURCE_LABELS,
  SUPPORTED_SOURCES,
  formatDate,
  formatNumber,
  formatScore,
  sentimentKind,
  sentimentText,
} from "../../shared/presentation";

function FilterResultRow({ result }) {
  const reasonText = FILTER_REASON_LABELS[result.reason] ?? result.reason;
  const decisionText = result.decision === "review_required" ? `${reasonText} 검토` : `${reasonText} 제외`;
  const methodText = result.classifier_kind === "rules_only" ? "규칙 판정" : "자동 판정";
  return <a className="article-row filter-result-row" href={result.url} target="_blank" rel="noreferrer">
    <span className={`filter-pill ${result.decision}`}>{decisionText}</span>
    <div><strong>{result.title}</strong><small>{SOURCE_LABELS[result.source] ?? result.source} · 판정 {formatDate(result.filtered_at)}</small><small className="filter-scores">관련성 {formatScore(result.relevance_score)} · 광고성 {formatScore(result.advertising_score)} · 신뢰도 {formatScore(result.confidence)} · {methodText}</small></div>
  </a>;
}

// 기업명을 눌렀을 때 해당 기업의 수집 기사와 필터 결과를 목록으로 보여준다.
function CollectedArticlesDialog({ company, days = null, onClose }) {
  const [page, setPage] = useState(1);
  const [displayMode, setDisplayMode] = useState("");
  const [data, setData] = useState(null);
  const [filtering, setFiltering] = useState({ rejected_count: 0, review_required_count: 0 });
  const [sources, setSources] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    const closeOnEscape = (event) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    const filterDecision = displayMode === FILTERED_DATA_MODE
      ? "rejected"
      : displayMode === REVIEW_DATA_MODE
        ? "review_required"
        : null;
    const sourceQuery = displayMode && !filterDecision ? `&source=${encodeURIComponent(displayMode)}` : "";
    const daysQuery = days && !filterDecision ? `&days=${encodeURIComponent(days)}` : "";
    const articleRequest = filterDecision
      ? api.get(`/companies/${company.id}/filter-results?decision=${filterDecision}&page=${page}&page_size=10`)
      : api.get(`/companies/${company.id}/articles?page=${page}&page_size=10${sourceQuery}${daysQuery}`);
    setData(null);
    Promise.all([articleRequest, api.get(`/companies/${company.id}/filter-summary`)])
      .then(([articleResponse, filterResponse]) => {
        if (!active) return;
        setData({ ...articleResponse.data, kind: filterDecision ? "filter_results" : "articles", decision: filterDecision });
        setFiltering(filterResponse.data);
        if (!filterDecision) setSources(articleResponse.data.sources ?? []);
        setError(null);
      })
      .catch((requestError) => { if (active) setError(getErrorMessage(requestError)); });
    return () => { active = false; };
  }, [company.id, days, displayMode, page]);

  const showingFilterResults = data?.kind === "filter_results";
  const title = data?.decision === "review_required" ? "검토 필요한 데이터" : showingFilterResults ? "필터링된 데이터" : "수집된 기사";
  const emptyText = data?.decision === "review_required" ? "검토가 필요한 데이터가 없습니다." : showingFilterResults ? "필터로 제외된 데이터가 없습니다." : "선택한 조건에 맞는 기사가 없습니다.";

  return <div className="collection-articles-modal-layer">
    <button className="collection-articles-backdrop" type="button" aria-label="수집 기사 목록 닫기" onClick={onClose} />
    <section className="collection-articles-modal" role="dialog" aria-modal="true" aria-labelledby="collection-articles-title">
      <header><div><span className="eyebrow">COLLECTED ARTICLES</span><h2 id="collection-articles-title">{company.name} {days ? `최근 ${days}일 ` : ""}수집 기사</h2><p>{title}를 최신순으로 확인합니다.</p></div><button className="collection-articles-close" type="button" onClick={onClose} aria-label="닫기">×</button></header>
      <label className="collection-articles-filter"><span>표시할 데이터</span><select value={displayMode} onChange={(event) => { setDisplayMode(event.target.value); setPage(1); }}><option value="">정제 통과 기사 전체</option><optgroup label="필터 판정"><option value={FILTERED_DATA_MODE}>필터 제외 데이터 · {formatNumber(filtering.rejected_count)}건</option><option value={REVIEW_DATA_MODE}>검토 필요 데이터 · {formatNumber(filtering.review_required_count)}건</option></optgroup><optgroup label="정제 통과 기사 출처">{sources.map((source) => <option value={source} key={source}>{SOURCE_LABELS[source] ?? source}</option>)}{SUPPORTED_SOURCES.filter((source) => !sources.includes(source)).map((source) => <option value={source} key={source} disabled>{SOURCE_LABELS[source]} · 수집 데이터 없음</option>)}</optgroup></select></label>
      {error && <div className="notice error">{error}</div>}
      {!data ? <p className="panel-empty">기사 목록을 불러오는 중입니다.</p> : <><div className="article-list collection-modal-article-list">{data.items.length ? (showingFilterResults ? data.items.map((result) => <FilterResultRow result={result} key={result.id} />) : data.items.map((article) => <a className="article-row" key={article.id} href={article.url} target="_blank" rel="noreferrer"><span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span><div><strong>{article.title}</strong><small>{SOURCE_LABELS[article.source] ?? article.source} · {formatDate(article.published_at ?? article.created_at)}</small></div></a>)) : <p className="panel-empty">{emptyText}</p>}</div><Pagination page={data.page} pageSize={data.page_size} total={data.total} onChange={setPage} /></>}
    </section>
  </div>;
}

// 전체 수집기 상태와 사용자별 기업의 실시간 수집 현황 및 제어 기능을 제공한다.
export default function CollectionPage({ onOpenCompany, initialArticleCompanyId = null, initialArticleDays = null, onMonitoringChanged }) {
  const [companies, setCompanies] = useState([]);
  const [summaries, setSummaries] = useState({});
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [articleCompany, setArticleCompany] = useState(null);
  const [pendingArticleCompanyId, setPendingArticleCompanyId] = useState(initialArticleCompanyId ? String(initialArticleCompanyId) : "");
  const { confirm, confirmationDialog } = useAppConfirm();

  const load = useCallback(async () => {
    try {
      const [companyResponse, healthResponse] = await Promise.all([api.get("/companies"), api.get("/collection-health")]);
      const nextCompanies = companyResponse.data;
      const summaryResults = await Promise.allSettled(nextCompanies.map((company) => api.get(`/companies/${company.id}/monitoring`)));
      setCompanies(nextCompanies); setHealth(healthResponse.data);
      setSummaries(Object.fromEntries(summaryResults.flatMap((result, index) => result.status === "fulfilled" ? [[nextCompanies[index].id, result.value.data]] : [])));
      setError(null);
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); const timer = window.setInterval(load, 30000); return () => window.clearInterval(timer); }, [load]);
  useEffect(() => {
    if (!pendingArticleCompanyId || !companies.length) return;
    const requestedCompany = companies.find((company) => String(company.id) === pendingArticleCompanyId);
    if (requestedCompany) setArticleCompany(requestedCompany);
    setPendingArticleCompanyId("");
  }, [companies, pendingArticleCompanyId]);

  const changeAll = async (action) => {
    const label = action === "pause" ? "정지" : "재개";
    const confirmed = await confirm({
      kicker: "COLLECTION CONTROL",
      title: `모든 기업의 실시간 수집을 ${label}할까요?`,
      message: "등록한 나의 기업과 모든 경쟁사에 적용됩니다.",
      confirmLabel: label,
      tone: action === "pause" ? "danger" : "default",
    });
    if (!confirmed) return;
    setBusy(`all-${action}`);
    try {
      await api.post(`/companies/monitoring/bulk/${action}`);
      await Promise.all([load(), onMonitoringChanged?.()]);
    }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBusy(null); }
  };
  const changeCompany = async (company) => {
    const action = company.monitoring_status === "paused" ? "resume" : "pause";
    setBusy(company.id);
    try {
      await api.post(`/companies/${company.id}/monitoring/${action}`);
      await Promise.all([load(), onMonitoringChanged?.()]);
    }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBusy(null); }
  };
  const runningCount = companies.filter((company) => ["backfilling", "warming", "active"].includes(company.monitoring_status)).length;
  const collectionRunning = runningCount > 0;
  const companyGroups = [
    { role: "main", title: "나의 기업", companies: companies.filter((company) => company.company_role === "main") },
    { role: "competitor", title: "경쟁사", companies: companies.filter((company) => company.company_role === "competitor") },
  ];
  const renderCompanyRow = (company) => {
    const summary = summaries[company.id];
    const canToggle = ["backfilling", "warming", "active", "paused"].includes(company.monitoring_status);
    return <article className="collection-company-row" key={company.id}>
      <div><span className={`status-dot ${company.monitoring_status}`} /><div><button className="collection-company-name" type="button" onClick={() => setArticleCompany(company)}>{company.name}</button><small>{company.industry_name} · {MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</small></div></div>
      <dl><div><dt>정제 기사</dt><dd>{formatNumber(summary?.article_count)}</dd></div><div><dt>분석 완료</dt><dd>{formatNumber(summary?.analyzed_count)}</dd></div><div><dt>마지막 수집</dt><dd>{formatDate(summary?.last_collected_at)}</dd></div></dl>
      <div className="collection-row-actions"><button type="button" onClick={() => onOpenCompany(company.id)}>분석 통계 보기</button>{canToggle && <button className={`collection-toggle ${company.monitoring_status === "paused" ? "start" : "stop"}`} type="button" onClick={() => changeCompany(company)} disabled={Boolean(busy)}>{busy === company.id ? "처리 중..." : company.monitoring_status === "paused" ? "수집 재개" : "수집 중지"}</button>}</div>
    </article>;
  };

  return <section className="workspace collection-workspace">
    <div className="workspace-head"><div><p>데이터 수집은 15분마다 실행되고 화면은 30초마다 갱신됩니다.</p></div></div>
    {error && <div className="notice error">{error}</div>}
    <div className="collection-summary-grid">
      <section className="panel collection-health-card"><PanelTitle kicker="COLLECTOR HEALTH" title="수집 시스템" />{health ? <><div className={`health-state ${health.status}`}><strong>{health.status === "healthy" ? "정상" : health.status === "degraded" ? "일부 장애" : health.status === "unavailable" ? "수집 불가" : "확인 전"}</strong><span>열린 장애 {formatNumber(health.open_incident_count)}건</span></div><div className="source-health-list">{health.sources.map((source) => <div key={source.source}><span>{SOURCE_LABELS[source.source] ?? source.source}</span><strong className={source.status}>{HEALTH_STATUS_LABELS[source.status] ?? source.status}</strong><small>연속 실패 {source.consecutive_failures}회</small></div>)}</div></> : <p className="panel-empty">수집기 상태를 불러오는 중입니다.</p>}</section>
      <section className="panel collection-control-card">
        <PanelTitle kicker="COLLECTION CONTROL" title="전체 수집 현황" />
        <div className="collection-status-row">
          <div className="collection-big-stat"><strong>{formatNumber(runningCount)}</strong><span>/ {formatNumber(companies.length)}개 기업 수집 활성</span></div>
          <div className={`collection-activity ${collectionRunning ? "running" : "stopped"}`} role="status" aria-live="polite">
            <div><span className="collection-activity-light" aria-hidden="true" /><strong>{collectionRunning ? "수집 진행 중" : "수집 중지됨"}</strong><small>{collectionRunning ? `${formatNumber(runningCount)}개 기업의 데이터를 수집하고 있습니다.` : "현재 실행 중인 기업 수집이 없습니다."}</small></div>
            <span className="collection-activity-track" aria-hidden="true"><i /></span>
          </div>
        </div>
        <p>기업별 기사와 댓글을 15분 단위로 수집하고 정제 파이프라인으로 전달합니다.</p>
        <div className="bulk-monitor-controls collection-bulk-controls"><button className="monitor-control stop" type="button" onClick={() => changeAll("pause")} disabled={Boolean(busy)}>{busy === "all-pause" ? "정지 중..." : "전체 정지"}</button><button className="monitor-control start" type="button" onClick={() => changeAll("resume")} disabled={Boolean(busy)}>{busy === "all-resume" ? "재개 중..." : "전체 재개"}</button></div>
      </section>
    </div>
    <section className="panel collection-company-section"><PanelTitle kicker="COMPANY STREAMS" title="기업별 수집 현황" /><p className="collection-company-guide">기업명을 누르면 해당 기업의 수집된 기사를 볼 수 있습니다.</p>{loading ? <p className="empty-state">수집 현황을 불러오는 중입니다.</p> : <div className="collection-stream-groups">{companyGroups.map((group) => <section className={`collection-stream-group ${group.role}`} key={group.role}><header><h3>{group.title}</h3></header>{group.companies.length ? <div className="collection-company-list">{group.companies.map(renderCompanyRow)}</div> : <p className="collection-group-empty">등록된 {group.title}이 없습니다.</p>}</section>)}</div>}</section>
    {articleCompany && <CollectedArticlesDialog company={articleCompany} days={initialArticleDays} onClose={() => setArticleCompany(null)} />}
    {confirmationDialog}
  </section>;
}
