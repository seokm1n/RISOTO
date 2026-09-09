import { useEffect, useMemo, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { useSharedResource } from "../../shared/useSharedResource";
import { Pagination, PanelTitle, useAppConfirm } from "../../shared/components";

import "./CollectionPage.css";
import {
  FILTERED_DATA_MODE,
  FILTER_REASON_LABELS,
  HEALTH_STATUS_LABELS,
  INCIDENT_STATUS_LABELS,
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

const INCIDENT_PAGE_SIZE = 100;

async function loadAllCollectionIncidents(status) {
  const firstResponse = await api.get(`/collection-incidents?status=${status}&page=1&page_size=${INCIDENT_PAGE_SIZE}`);
  const firstItems = firstResponse.data?.items ?? [];
  const total = firstResponse.data?.total ?? firstItems.length;
  const pageCount = Math.ceil(total / INCIDENT_PAGE_SIZE);
  if (pageCount <= 1) return firstItems;

  const remainingResponses = await Promise.all(
    Array.from({ length: pageCount - 1 }, (_, index) => (
      api.get(`/collection-incidents?status=${status}&page=${index + 2}&page_size=${INCIDENT_PAGE_SIZE}`)
    )),
  );
  return [
    ...firstItems,
    ...remainingResponses.flatMap((response) => response.data?.items ?? []),
  ];
}

function incidentReasonText(incident) {
  const detail = (incident.error_summary ?? "").toLowerCase();
  const sources = (incident.sources ?? []).map((source) => SOURCE_LABELS[source] ?? source).join(", ") || "수집기";
  if (/401|403|unauthorized/.test(detail)) return `${sources} API 인증에 실패했습니다. Client ID와 Client Secret을 확인해 주세요.`;
  if (/429|rate.?limit|too many requests/.test(detail)) return `${sources} API 요청 한도를 초과했습니다.`;
  if (/timeout|timed out/.test(detail)) return `${sources} 응답 시간이 초과되었습니다.`;
  if (/connection|network|dns/.test(detail)) return `${sources} 연결에 실패했습니다.`;
  if (/데이터 저장 충돌/.test(detail)) return "수집 데이터 저장 중 충돌이 발생했습니다.";
  if (/데이터베이스 작업 실패/.test(detail)) return "수집 데이터 저장 작업에 실패했습니다.";
  return incident.error_summary || `${sources} 수집 과정에서 오류가 발생했습니다.`;
}

function CollectionIncidentItem({ title, status, statusLabel, children }) {
  return <details className="collection-incident-item">
    <summary className="collection-incident-heading">
      <strong>{title}</strong>
      <span className={`collection-incident-status ${status}`}>{statusLabel}</span>
      <span className="collection-incident-toggle" aria-hidden="true"><span className="when-closed">상세 보기</span><span className="when-open">접기</span></span>
    </summary>
    <div className="collection-incident-body">{children}</div>
  </details>;
}

function CollectionIncidentSummary({ companies, health, incidents }) {
  const companyNames = useMemo(() => new Map(companies.map((company) => [company.id, company.name])), [companies]);
  const failingSources = (health?.sources ?? []).filter((source) => ["partial", "down"].includes(source.status));
  const status = health?.status ?? "unknown";
  return <section className={`collection-incident-summary ${status}`} aria-live="polite">
    <span className="collection-incident-indicator" aria-hidden="true" />
    <div className="collection-incident-details">
      {!health ? <article><strong>수집 장애 상태를 확인하고 있습니다.</strong></article> : incidents.length ? incidents.map((incident) => {
        const affectedCompanyIds = incident.affected_company_ids ?? [];
        const affectedNames = affectedCompanyIds.map((id) => companyNames.get(id)).filter(Boolean);
        const affectedText = affectedNames.length
          ? affectedNames.join(", ")
          : affectedCompanyIds.length
            ? `영향 기업 ${formatNumber(affectedCompanyIds.length)}곳`
            : "전체 수집 시스템";
        const sourceText = (incident.sources ?? []).map((source) => SOURCE_LABELS[source] ?? source).join(", ") || "수집기";
        return <CollectionIncidentItem key={incident.id} title={incidentReasonText(incident)} status={incident.status} statusLabel={INCIDENT_STATUS_LABELS[incident.status] ?? incident.status}>
          <dl className="collection-incident-fields">
            <div><dt>수집기</dt><dd>{sourceText}</dd></div>
            <div><dt>영향 기업</dt><dd>{affectedText}</dd></div>
            <div><dt>수집 구간</dt><dd>{formatDate(incident.scheduled_for)}</dd></div>
            <div><dt>감지 시각</dt><dd>{formatDate(incident.detected_at)}</dd></div>
            <div><dt>마지막 확인</dt><dd>{formatDate(incident.last_seen_at)}</dd></div>
            <div><dt>재시도 횟수</dt><dd>{formatNumber(incident.retry_count)}회</dd></div>
            <div><dt>다음 재시도</dt><dd>{incident.next_retry_at ? formatDate(incident.next_retry_at) : "예약 없음"}</dd></div>
          </dl>
          <div className="collection-incident-error"><strong>저장된 오류 내용</strong><p>{incident.error_summary || "추가 오류 내용이 없습니다."}</p></div>
        </CollectionIncidentItem>;
      }) : failingSources.length ? failingSources.map((source) => <CollectionIncidentItem key={source.source} title={incidentReasonText({ error_summary: source.last_error_message, sources: [source.source] })} status={source.status === "down" ? "open" : "retrying"} statusLabel={HEALTH_STATUS_LABELS[source.status] ?? source.status}>
        <dl className="collection-incident-fields">
          <div><dt>수집기</dt><dd>{SOURCE_LABELS[source.source] ?? source.source}</dd></div>
          <div><dt>연속 실패</dt><dd>{formatNumber(source.consecutive_failures)}회</dd></div>
          <div><dt>마지막 시도</dt><dd>{formatDate(source.last_attempt_at)}</dd></div>
          <div><dt>마지막 성공</dt><dd>{formatDate(source.last_success_at)}</dd></div>
          {source.last_error_code && <div><dt>오류 코드</dt><dd>{source.last_error_code}</dd></div>}
        </dl>
        <div className="collection-incident-error"><strong>저장된 오류 내용</strong><p>{source.last_error_message || "추가 오류 내용이 없습니다."}</p></div>
      </CollectionIncidentItem>) : <article><strong>현재 확인된 수집 장애가 없습니다.</strong><p>모든 수집기가 정상적으로 응답하고 있습니다.</p></article>}
    </div>
  </section>;
}

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
export function CollectedArticlesDialog({ company, days = null, windowRange = null, onClose }) {
  const [page, setPage] = useState(1);
  const [displayMode, setDisplayMode] = useState("");
  const [sources, setSources] = useState([]);

  useEffect(() => {
    const closeOnEscape = (event) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const { data: snapshot, error: loadError } = useSharedResource(
    `collected-articles:${JSON.stringify([company.id, days, windowRange?.start, windowRange?.end, displayMode, page])}`,
    async () => {
      const filterDecision = displayMode === FILTERED_DATA_MODE
        ? "rejected"
        : displayMode === REVIEW_DATA_MODE
          ? "review_required"
          : null;
      const sourceQuery = displayMode && !filterDecision ? `&source=${encodeURIComponent(displayMode)}` : "";
      const daysQuery = days && !filterDecision ? `&days=${encodeURIComponent(days)}` : "";
      const windowQuery = windowRange && !filterDecision
        ? `&time_from=${encodeURIComponent(windowRange.start)}&time_to=${encodeURIComponent(windowRange.end)}`
        : "";
      const articleRequest = filterDecision
        ? api.get(`/companies/${company.id}/filter-results?decision=${filterDecision}&page=${page}&page_size=10`)
        : api.get(`/companies/${company.id}/articles?page=${page}&page_size=10${sourceQuery}${daysQuery}${windowQuery}`);
      const [articleResponse, filterResponse] = await Promise.all([articleRequest, api.get(`/companies/${company.id}/filter-summary`)]);
      return { data: { ...articleResponse.data, kind: filterDecision ? "filter_results" : "articles", decision: filterDecision }, filtering: filterResponse.data };
    },
  );
  const { data = null, filtering = { rejected_count: 0, review_required_count: 0 } } = snapshot ?? {};
  const error = loadError ? getErrorMessage(loadError) : null;
  useEffect(() => { if (data?.kind === "articles") setSources(data.sources ?? []); }, [data]);

  const showingFilterResults = data?.kind === "filter_results";
  const title = data?.decision === "review_required" ? "검토 필요한 데이터" : showingFilterResults ? "필터링된 데이터" : "수집된 기사";
  const emptyText = data?.decision === "review_required" ? "검토가 필요한 데이터가 없습니다." : showingFilterResults ? "필터로 제외된 데이터가 없습니다." : "선택한 조건에 맞는 기사가 없습니다.";

  return <div className="collection-articles-modal-layer">
    <button className="collection-articles-backdrop" type="button" aria-label="수집 기사 목록 닫기" onClick={onClose} />
    <section className="collection-articles-modal" role="dialog" aria-modal="true" aria-labelledby="collection-articles-title">
      <header><div><h2 id="collection-articles-title">{company.name} {windowRange ? `${formatDate(windowRange.start)} 구간 ` : days ? `최근 ${days}일 ` : ""}수집 기사</h2><p>{windowRange ? `${formatDate(windowRange.start)}부터 15분 동안의 ${title}` : `${title}를 최신순으로 확인합니다.`}</p></div><button className="collection-articles-close" type="button" onClick={onClose} aria-label="닫기">×</button></header>
      <label className="collection-articles-filter"><span>표시할 데이터</span><select value={displayMode} onChange={(event) => { setDisplayMode(event.target.value); setPage(1); }}><option value="">정제 통과 기사 전체</option>{!windowRange && <optgroup label="필터 판정"><option value={FILTERED_DATA_MODE}>필터 제외 데이터 · {formatNumber(filtering.rejected_count)}건</option><option value={REVIEW_DATA_MODE}>검토 필요 데이터 · {formatNumber(filtering.review_required_count)}건</option></optgroup>}<optgroup label="정제 통과 기사 출처">{sources.map((source) => <option value={source} key={source}>{SOURCE_LABELS[source] ?? source}</option>)}{SUPPORTED_SOURCES.filter((source) => !sources.includes(source)).map((source) => <option value={source} key={source} disabled>{SOURCE_LABELS[source]} · 수집 데이터 없음</option>)}</optgroup></select></label>
      {error && <div className="notice error">{error}</div>}
      {!data ? <p className="panel-empty">기사 목록을 불러오는 중입니다.</p> : <><div className="article-list collection-modal-article-list">{data.items.length ? (showingFilterResults ? data.items.map((result) => <FilterResultRow result={result} key={result.id} />) : data.items.map((article) => <a className="article-row" key={article.id} href={article.url} target="_blank" rel="noreferrer"><span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span><div><strong>{article.title}</strong><small>{SOURCE_LABELS[article.source] ?? article.source} · {formatDate(article.published_at ?? article.created_at)}</small></div></a>)) : <p className="panel-empty">{emptyText}</p>}</div><Pagination page={data.page} pageSize={data.page_size} total={data.total} onChange={setPage} /></>}
    </section>
  </div>;
}

// 전체 수집기 상태와 사용자별 기업의 실시간 수집 현황 및 제어 기능을 제공한다.
export default function CollectionPage({ onOpenCompany, initialArticleCompanyId = null, initialArticleDays = null, onMonitoringChanged }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [articleCompany, setArticleCompany] = useState(null);
  const [pendingArticleCompanyId, setPendingArticleCompanyId] = useState(initialArticleCompanyId ? String(initialArticleCompanyId) : "");
  const { confirm, confirmationDialog } = useAppConfirm();

  const { data: snapshot, loading, error: loadError, refresh } = useSharedResource("collection:overview", async () => {
    const [companyResponse, healthResponse, openIncidents, retryingIncidents] = await Promise.all([
      api.get("/companies"), api.get("/collection-health"),
      loadAllCollectionIncidents("open"), loadAllCollectionIncidents("retrying"),
    ]);
    const companies = companyResponse.data;
    const results = await Promise.allSettled(companies.map((company) => api.get(`/companies/${company.id}/monitoring`)));
    return {
      companies, health: healthResponse.data,
      incidents: [...openIncidents, ...retryingIncidents].sort((left, right) => new Date(right.last_seen_at ?? right.detected_at) - new Date(left.last_seen_at ?? left.detected_at)),
      summaries: Object.fromEntries(results.flatMap((result, index) => {
        const id = companies[index].id;
        const data = result.status === "fulfilled" ? result.value.data : snapshot?.summaries[id];
        return data === undefined ? [] : [[id, data]];
      })),
    };
  });
  const { companies = [], health = null, incidents = [], summaries = {} } = snapshot ?? {};
  const load = async () => { try { await refresh(); setError(null); } catch (requestError) { setError(getErrorMessage(requestError)); } };

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
      message: "등록한 나의 기업과 모든 비교 기업에 적용됩니다.",
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
  const unhealthySourceCount = (health?.sources ?? []).filter((source) => ["partial", "down"].includes(source.status)).length;
  const companyGroups = [
    { role: "main", title: "나의 기업", companies: companies.filter((company) => company.company_role === "main") },
    { role: "competitor", title: "비교 기업", companies: companies.filter((company) => company.company_role === "competitor") },
  ];
  const renderCompanyRow = (company) => {
    const summary = summaries[company.id];
    const canToggle = ["backfilling", "warming", "active", "paused"].includes(company.monitoring_status);
    return <article className="collection-company-row" onClick={(event) => {
      if (event.target.closest("button, a, input, select, textarea, summary, [role='button'], [role='link']")) return;
      setArticleCompany(company);
    }} key={company.id}>
      <div><span className={`status-dot ${company.monitoring_status}`} /><div><div className="collection-name-row"><button className="collection-company-name" type="button" onClick={() => setArticleCompany(company)}>{company.name}</button><span className={`mg-role-badge ${company.company_role}`}>{company.company_role === "main" ? "나의 기업" : "비교 기업"}</span></div><small>{company.industry_name} · <span className={`mg-status-pill ${company.monitoring_status}`}>{MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</span></small></div></div>
      <dl><div><dt>마지막 수집</dt><dd>{formatDate(summary?.last_collected_at)}</dd></div></dl>
      <div className="collection-row-actions"><button className="collection-history-button" type="button" aria-label={`${company.name} 수집 이력 보기`} onClick={() => onOpenCompany(company.id)}>수집 이력 보기</button>{canToggle && <button className={`collection-toggle ${company.monitoring_status === "paused" ? "start" : "stop"}`} type="button" onClick={() => changeCompany(company)} disabled={Boolean(busy)}>{busy === company.id ? "처리 중..." : company.monitoring_status === "paused" ? "수집 재개" : "수집 중지"}</button>}</div>
    </article>;
  };

  return <section className="workspace collection-workspace">
    <div className="workspace-head"><div><p>데이터 수집은 15분마다 실행되고 화면은 30초마다 갱신됩니다.</p></div></div>
    {(error || loadError) && <div className="notice error">{error || getErrorMessage(loadError)}</div>}
    <div className="collection-summary-grid">
      <section className="panel collection-health-card"><PanelTitle title="수집 시스템" />{health ? <><div className={`health-state ${health.status}`}><strong>{health.status === "healthy" ? "정상" : health.status === "degraded" ? "일부 장애" : health.status === "unavailable" ? "수집 불가" : "확인 전"}</strong><span>오류 발생 수집기 {formatNumber(unhealthySourceCount)}개{health.open_incident_count > 0 ? ` · 미해결 장애 ${formatNumber(health.open_incident_count)}건` : ""}</span></div><div className="source-health-list">{health.sources.map((source) => <div key={source.source}><span>{SOURCE_LABELS[source.source] ?? source.source}</span><strong className={source.status}>{HEALTH_STATUS_LABELS[source.status] ?? source.status}</strong><small>연속 실패 {source.consecutive_failures}회</small></div>)}</div></> : <p className="panel-empty">수집기 상태를 불러오는 중입니다.</p>}</section>
      <section className="panel collection-control-card">
        <PanelTitle icon="collection" title="전체 수집 현황" />
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
    <CollectionIncidentSummary companies={companies} health={health} incidents={incidents} />
    <section className="panel collection-company-section"><PanelTitle icon="companies" title="기업별 수집 현황" />{loading ? <p className="empty-state">수집 현황을 불러오는 중입니다.</p> : <div className="collection-stream-groups">{companyGroups.map((group) => <section className={`collection-stream-group ${group.role}`} key={group.role}><header><h3>{group.title}</h3></header>{group.companies.length ? <div className="collection-company-list">{group.companies.map(renderCompanyRow)}</div> : <p className="collection-group-empty">등록된 {group.title}이 없습니다.</p>}</section>)}</div>}</section>
    {articleCompany && <CollectedArticlesDialog company={articleCompany} days={initialArticleDays} onClose={() => setArticleCompany(null)} />}
    {confirmationDialog}
  </section>;
}
