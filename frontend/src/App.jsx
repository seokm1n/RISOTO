import { useCallback, useEffect, useMemo, useState } from "react";

import { api, getErrorMessage } from "./api";

const KEYWORD_LABELS = { peer: "유사기업", product: "제품·브랜드" };
const MONITORING_LABELS = {
  backfilling: "7일 과거 수집 중",
  warming: "탐지 기준 학습 중",
  active: "실시간 탐지 활성",
  paused: "모니터링 일시중지",
  archived: "보관됨",
  error: "설정 확인 필요",
};

const formatNumber = (value) => new Intl.NumberFormat("ko-KR").format(value ?? 0);
const formatDate = (value) => value ? new Date(value).toLocaleString("ko-KR", {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
}) : "-";
const formatCountdown = (seconds) => {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  return `${Math.floor(safeSeconds / 60)}분 ${String(safeSeconds % 60).padStart(2, "0")}초`;
};
const sentimentKind = (label) => ["positive", "긍정"].includes(label) ? "positive" : ["negative", "부정"].includes(label) ? "negative" : "pending";
const sentimentText = (label) => sentimentKind(label) === "positive" ? "긍정" : sentimentKind(label) === "negative" ? "부정" : "분석 대기";

function KeywordInput({ id, label, hint, values, onChange }) {
  const [draft, setDraft] = useState("");
  const addValue = () => {
    const normalized = draft.trim().replace(/\s+/g, " ");
    if (!normalized) return;
    if (!values.some((value) => value.toLocaleLowerCase() === normalized.toLocaleLowerCase())) {
      onChange([...values, normalized]);
    }
    setDraft("");
  };

  return (
    <div>
      <label htmlFor={id} className="field-label">{label}</label>
      <div className="keyword-shell focus-within:ring-2 focus-within:ring-[#1f5c4a]/25">
        {values.map((value) => (
          <span className="keyword-chip" key={value}>
            {value}<button type="button" aria-label={`${value} 삭제`} onClick={() => onChange(values.filter((item) => item !== value))}>×</button>
          </span>
        ))}
        <input id={id} value={draft} onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === ",") { event.preventDefault(); addValue(); }
            if (event.key === "Backspace" && !draft && values.length) onChange(values.slice(0, -1));
          }}
          onBlur={addValue} placeholder={values.length ? "추가 입력" : hint} autoComplete="off" />
      </div>
      <p className="field-hint">Enter 또는 쉼표로 여러 항목을 추가할 수 있습니다.</p>
    </div>
  );
}

function CompanyCard({ company, monitoringSummary }) {
  const grouped = useMemo(() => {
    const result = { peer: [], product: [] };
    company.keywords.forEach((keyword) => result[keyword.keyword_type]?.push(keyword.value));
    return result;
  }, [company]);
  return (
    <article className="company-card">
      <div className="flex items-start justify-between gap-5">
        <div><span className="eyebrow">ACTIVE MONITOR</span><h3>{company.name}</h3><p>{company.industry_name}</p></div>
        <span className={`status-dot ${company.monitoring_status}`} />
      </div>
      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        {Object.entries(KEYWORD_LABELS).map(([type, label]) => <div key={type}>
          <span className="mini-label">{label}</span><p className="mt-1 text-sm leading-6 text-[#3d4944]">{grouped[type].join(" · ") || "등록 없음"}</p>
        </div>)}
      </div>
      <div className={`pipeline-state ${company.monitoring_status}`}>
        <strong>{MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</strong>
        <span>감성분석: {company.analysis_status}</span>
        {monitoringSummary && <small>수집 {monitoringSummary.article_count}건 · 분석 {monitoringSummary.analyzed_count}건 · 이상 {monitoringSummary.anomaly_count}건</small>}
      </div>
    </article>
  );
}

function SetupPage() {
  const [industries, setIndustries] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [name, setName] = useState("");
  const [industryId, setIndustryId] = useState("");
  const [peers, setPeers] = useState([]);
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState(null);
  const [monitoringSummaries, setMonitoringSummaries] = useState({});

  const loadData = useCallback(async () => {
    try {
      const [industryResponse, companyResponse] = await Promise.all([api.get("/industries"), api.get("/companies")]);
      setIndustries(industryResponse.data); setCompanies(companyResponse.data);
      const results = await Promise.allSettled(companyResponse.data.map((company) => api.get(`/companies/${company.id}/monitoring`)));
      setMonitoringSummaries(Object.fromEntries(results.flatMap((result, index) => result.status === "fulfilled" ? [[companyResponse.data[index].id, result.value.data]] : [])));
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadData(); const timer = window.setInterval(loadData, 15000); return () => window.clearInterval(timer); }, [loadData]);

  const submit = async (event) => {
    event.preventDefault();
    if (!name.trim() || !industryId) { setNotice({ type: "error", message: "기업명과 산업군을 입력해 주세요." }); return; }
    setSubmitting(true); setNotice(null);
    try {
      const response = await api.post("/companies", { name: name.trim(), industry_id: Number(industryId), backfill_days: 7,
        keywords: [...peers.map((value) => ({ keyword_type: "peer", value })), ...products.map((value) => ({ keyword_type: "product", value }))] });
      setName(""); setIndustryId(""); setPeers([]); setProducts([]); await loadData();
      setNotice({ type: "success", message: response.data.is_existing ? `${response.data.name}에 새 키워드 ${response.data.added_keyword_count}개를 반영했습니다.` : `${response.data.name}의 과거·실시간 수집을 시작했습니다.` });
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setSubmitting(false); }
  };

  return <>
    <section className="hero-grid">
      <div className="hero-copy"><span className="eyebrow">MONITOR SETUP / 01</span><h1>관찰할 기업의<br /><em>문맥</em>을 정의하세요.</h1>
        <p>기업명은 기본 검색어로 사용합니다. 유사기업과 제품명을 더하면 수집 범위를 넓히면서도 관련성을 유지할 수 있습니다.</p>
        <div className="sequence"><span className="active">01 기업 설정</span><span>02 과거 수집</span><span>03 실시간 탐지</span></div>
      </div>
      <form className="setup-card" onSubmit={submit}>
        <div className="card-heading"><div><span className="eyebrow">NEW TARGET</span><h2>기업 등록</h2></div><span className="step-number">01</span></div>
        <div className="form-grid">
          <div><label className="field-label" htmlFor="company-name">기업명</label><input className="text-field" id="company-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="기업명" maxLength={200} required /></div>
          <div><label className="field-label" htmlFor="industry">산업군</label><select className="text-field" id="industry" value={industryId} onChange={(event) => setIndustryId(event.target.value)} required><option value="">산업군을 선택</option>{industries.map((industry) => <option value={industry.id} key={industry.id}>{industry.name}</option>)}</select></div>
          <KeywordInput id="peer-keywords" label="유사기업" hint="선택 입력" values={peers} onChange={setPeers} />
          <KeywordInput id="product-keywords" label="검색 키워드" hint="제품명·브랜드명" values={products} onChange={setProducts} />
        </div>
        {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
        <button className="submit-button" type="submit" disabled={submitting || loading}><span>{submitting ? "등록 중..." : "모니터링 대상 등록"}</span><b aria-hidden="true">→</b></button>
      </form>
    </section>
    <section className="registered-section"><div className="section-title"><div><span className="eyebrow">REGISTERED TARGETS</span><h2>등록한 기업</h2></div><strong>{companies.length.toString().padStart(2, "0")}</strong></div>
      {loading ? <p className="empty-state">기업 정보를 불러오는 중입니다.</p> : companies.length ? <div className="company-list">{companies.map((company) => <CompanyCard company={company} key={company.id} monitoringSummary={monitoringSummaries[company.id]} />)}</div> : <p className="empty-state">아직 등록한 기업이 없습니다.</p>}
    </section>
  </>;
}

function RealtimePage() {
  const [companies, setCompanies] = useState([]); const [selectedId, setSelectedId] = useState("");
  const [data, setData] = useState(null); const [error, setError] = useState(null);
  const [articlePage, setArticlePage] = useState(1); const [jobPage, setJobPage] = useState(1);
  const [changingState, setChangingState] = useState(false);
  const [now, setNow] = useState(Date.now());
  const refresh = useCallback(async () => {
    try {
      const companyResponse = await api.get("/companies"); const nextCompanies = companyResponse.data;
      setCompanies(nextCompanies); const id = selectedId || nextCompanies[0]?.id;
      if (!id) { setData(null); return; }
      if (!selectedId) setSelectedId(String(id));
      const [monitoring, jobs, articles, risks] = await Promise.all([api.get(`/companies/${id}/monitoring`), api.get(`/companies/${id}/collection-jobs?page=${jobPage}&page_size=10`), api.get(`/companies/${id}/articles?page=${articlePage}&page_size=10`), api.get(`/companies/${id}/risk-events?limit=10`)]);
      setData({ monitoring: monitoring.data, jobs: jobs.data, articles: articles.data, risks: risks.data }); setError(null);
    } catch (requestError) { setError(getErrorMessage(requestError)); }
  }, [selectedId, articlePage, jobPage]);
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 15000); return () => window.clearInterval(timer); }, [refresh]);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const selected = companies.find((company) => String(company.id) === String(selectedId));
  const secondsUntilCollection = data?.monitoring.next_collection_at ? (new Date(data.monitoring.next_collection_at).getTime() - now) / 1000 : null;
  const changeMonitoringState = async () => {
    if (!selected) return;
    setChangingState(true);
    try {
      await api.post(`/companies/${selected.id}/monitoring/${selected.monitoring_status === "paused" ? "resume" : "pause"}`);
      await refresh();
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setChangingState(false); }
  };
  return <section className="workspace"><div className="workspace-head"><div><span className="eyebrow">LIVE COLLECTION / 02</span><h1>실시간 수집</h1><p>기사는 15분마다 수집하고, 화면 현황은 15초마다 갱신합니다.</p></div><span className="live-indicator"><i /> LIVE</span></div>
    <div className="monitor-toolbar"><label>모니터링 기업<select value={selectedId} onChange={(event) => { setSelectedId(event.target.value); setArticlePage(1); setJobPage(1); }}>{companies.map((company) => <option key={company.id} value={company.id}>{company.name} · {company.industry_name}</option>)}</select></label>{data?.monitoring.next_collection_at && <div className="collection-countdown"><span>다음 기사 수집까지</span><strong>{formatCountdown(secondsUntilCollection)}</strong><small>15분 주기</small></div>}{selected && <><span className={`state-badge ${selected.monitoring_status}`}>{MONITORING_LABELS[selected.monitoring_status]}</span><button className={`monitor-control ${selected.monitoring_status === "paused" ? "resume" : "stop"}`} onClick={changeMonitoringState} disabled={changingState}>{changingState ? "처리 중..." : selected.monitoring_status === "paused" ? "실시간 탐지 재개" : "실시간 탐지 중지"}</button></>}</div>
    {error && <div className="notice error">{error}</div>}
    {!selected ? <p className="empty-state">먼저 기업 등록 페이지에서 모니터링할 기업을 등록해 주세요.</p> : data && <>
      <div className="metric-grid"><Metric label="수집 기사" value={data.monitoring.article_count} /><Metric label="감성 분석 완료" value={data.monitoring.analyzed_count} /><Metric label="이상 신호" value={data.monitoring.anomaly_count} tone={data.monitoring.anomaly_count ? "danger" : ""} /><Metric label="마지막 수집" value={formatDate(data.monitoring.last_collected_at)} small /></div>
      <div className="live-grid"><section className="panel span-two"><PanelTitle kicker="COLLECTED ARTICLES" title="수집된 기사" /><div className="article-list">{data.articles.items.length ? data.articles.items.map((article) => <a className="article-row" key={article.id} href={article.url} target="_blank" rel="noreferrer"><span className={`sentiment-pill ${article.sentiment_label ?? "pending"}`}>{sentimentText(article.sentiment_label)}</span><div><strong>{article.title}</strong><small>{article.source} · {formatDate(article.published_at ?? article.created_at)}</small></div>{article.is_anomaly && <span className="risk-mark">위험</span>}</a>) : <p className="panel-empty">아직 수집된 기사가 없습니다.</p>}</div><Pagination page={data.articles.page} pageSize={data.articles.page_size} total={data.articles.total} onChange={setArticlePage} /></section>
        <section className="panel"><PanelTitle kicker="COLLECTION LOG" title="수집 이력" /><div className="job-list">{data.jobs.items.map((job) => <div className="job-row" key={job.id}><span className={`job-status ${job.status}`} /> <div><strong>{job.job_type === "realtime" ? "실시간" : job.job_type === "backfill" ? "과거 수집" : "수동 수집"}</strong><small>{formatDate(job.completed_at ?? job.started_at)} · 신규 {job.new_count}건</small></div></div>)}</div><Pagination page={data.jobs.page} pageSize={data.jobs.page_size} total={data.jobs.total} onChange={setJobPage} /></section>
        <section className="panel"><PanelTitle kicker="RISK EVENTS" title="위험 알림" /><div className="risk-list">{data.risks.length ? data.risks.map((risk) => <a href={risk.article_url} target="_blank" rel="noreferrer" key={risk.id}><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><strong>{risk.article_title}</strong><small>이상 점수 {risk.anomaly_score.toFixed(2)} · {formatDate(risk.detected_at)}</small></a>) : <p className="panel-empty">새 위험 알림이 없습니다.</p>}</div></section>
      </div>
    </>}
  </section>;
}

function Metric({ label, value, tone = "", small = false }) { return <article className={`metric ${tone}`}><span>{label}</span><strong className={small ? "metric-date" : ""}>{small ? value : formatNumber(value)}</strong></article>; }
function Pagination({ page, pageSize, total, onChange }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return total ? <p className="page-count">총 {formatNumber(total)}건</p> : null;
  return <div className="pagination"><span>{page} / {pages} · 총 {formatNumber(total)}건</span><button type="button" onClick={() => onChange(page - 1)} disabled={page <= 1}>이전</button><button type="button" onClick={() => onChange(page + 1)} disabled={page >= pages}>다음</button></div>;
}
function PanelTitle({ kicker, title }) { return <div className="panel-title"><span className="eyebrow">{kicker}</span><h2>{title}</h2></div>; }

function DashboardPage() {
  const [overview, setOverview] = useState(null); const [days, setDays] = useState(7); const [error, setError] = useState(null);
  const refresh = useCallback(async () => { try { const response = await api.get(`/dashboard/overview?days=${days}`); setOverview(response.data); setError(null); } catch (requestError) { setError(getErrorMessage(requestError)); } }, [days]);
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 30000); return () => window.clearInterval(timer); }, [refresh]);
  const maximum = Math.max(...(overview?.daily.map((item) => item.article_count) ?? [1]), 1);
  const sentimentTotal = overview?.sentiments.reduce((sum, item) => sum + item.count, 0) ?? 0;
  return <section className="workspace"><div className="workspace-head"><div><span className="eyebrow">ANALYTICS / 03</span><h1>통계 대시보드</h1><p>수집량, 감성 흐름, 탐지된 위험 신호를 한눈에 확인합니다.</p></div><label className="range-select">기간<select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={7}>최근 7일</option><option value={14}>최근 14일</option><option value={30}>최근 30일</option></select></label></div>
    {error && <div className="notice error">{error}</div>}
    {!overview ? <p className="empty-state">통계를 불러오는 중입니다.</p> : <><div className="metric-grid"><Metric label="모니터링 기업" value={overview.total_companies} /><Metric label="실시간 탐지 활성" value={overview.active_companies} /><Metric label={`${days}일 수집 기사`} value={overview.article_count} /><Metric label="위험 이벤트" value={overview.risk_count} tone={overview.risk_count ? "danger" : ""} /></div>
      <div className="dashboard-grid"><section className="panel chart-panel"><PanelTitle kicker="COLLECTION VOLUME" title="일별 수집량" /><div className="bar-chart">{overview.daily.length ? overview.daily.map((item) => <div className="bar-item" key={item.day}><div className="bar-value">{item.article_count}</div><div className="bar-track"><i style={{ height: `${Math.max(6, item.article_count / maximum * 100)}%` }} /></div><small>{new Date(item.day).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}</small></div>) : <p className="panel-empty">기간 내 수집 기사가 없습니다.</p>}</div></section>
        <section className="panel"><PanelTitle kicker="SENTIMENT" title="감성 분포" /><div className="sentiment-summary">{["positive", "negative"].map((label) => { const count = overview.sentiments.find((item) => item.label === label)?.count ?? 0; return <div key={label}><span className={`sentiment-pill ${label}`}>{sentimentText(label)}</span><strong>{formatNumber(count)}건</strong><small>{sentimentTotal ? Math.round(count / sentimentTotal * 100) : 0}%</small></div>; })}</div><p className="dashboard-note">KoELECTRA 분석 완료 기사 기준</p></section>
        <section className="panel span-two"><PanelTitle kicker="COMPANY STATUS" title="기업별 모니터링 현황" /><div className="company-table"><div className="table-head"><span>기업</span><span>상태</span><span>기사</span><span>부정</span><span>위험</span></div>{overview.companies.map((company) => <div className="table-row" key={company.id}><strong>{company.name}</strong><span className={`state-badge ${company.monitoring_status}`}>{MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</span><span>{formatNumber(company.article_count)}</span><span>{formatNumber(company.negative_count)}</span><span>{formatNumber(company.risk_count)}</span></div>)}</div></section>
        <section className="panel"><PanelTitle kicker="LATEST ALERTS" title="최근 위험 이벤트" /><div className="risk-list">{overview.recent_risks.length ? overview.recent_risks.map((risk) => <a href={risk.article_url} target="_blank" rel="noreferrer" key={risk.id}><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><strong>{risk.article_title}</strong><small>{formatDate(risk.detected_at)}</small></a>) : <p className="panel-empty">탐지된 위험 이벤트가 없습니다.</p>}</div></section>
      </div>
    </>}
  </section>;
}

export default function App() {
  const [page, setPage] = useState("setup");
  return <main className="min-h-screen"><header className="topbar"><button className="brand" onClick={() => setPage("setup")}>RISOTO<span>RISK INTELLIGENCE</span></button><nav className="main-nav">{[["setup", "기업 등록"], ["realtime", "실시간 수집"], ["dashboard", "통계 대시보드"]].map(([id, label]) => <button className={page === id ? "active" : ""} onClick={() => setPage(id)} key={id}>{label}</button>)}</nav><div className="system-state"><i /> SYSTEM READY</div></header>{page === "setup" && <SetupPage />}{page === "realtime" && <RealtimePage />}{page === "dashboard" && <DashboardPage />}</main>;
}
