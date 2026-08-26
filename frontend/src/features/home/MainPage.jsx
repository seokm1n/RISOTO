import { useCallback, useEffect, useMemo, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { formatDate, READINESS_LABELS, SOURCE_LABELS } from "../../shared/presentation";

const GRAPH_WIDTH = 360;
const GRAPH_HEIGHT = 132;
const GRAPH_PADDING = { top: 12, right: 12, bottom: 20, left: 12 };

const dailySeries = (summaries = []) => [...summaries]
  .sort((left, right) => String(left.summary_date).localeCompare(String(right.summary_date)))
  .map((summary) => ({
    day: String(summary.summary_date),
    article_count: Number(summary.article_count) || 0,
    risk_event_count: Number(summary.risk_event_count) || 0,
  }));

const aggregateDailySeries = (summaryGroups) => summaryGroups.flat().reduce((result, item) => {
  const current = result.get(item.day) ?? { day: item.day, article_count: 0, risk_event_count: 0 };
  current.article_count += item.article_count;
  current.risk_event_count += item.risk_event_count;
  result.set(item.day, current);
  return result;
}, new Map());

const articleTimestamp = (article) => new Date(article.published_at ?? article.created_at ?? 0).getTime() || 0;
const articleSummary = (article) => {
  const text = article.summary?.trim();
  return text ? (text.length > 160 ? `${text.slice(0, 160)}…` : text) : "수집된 기사에 별도 요약이 없습니다.";
};

function TrendGraphs({ data, label }) {
  const collectionValues = data.map((item) => item.article_count);
  const riskValues = data.map((item) => item.risk_event_count);
  const collectionMax = Math.max(...collectionValues, 1);
  const riskMax = Math.max(...riskValues, 1);
  const plotWidth = GRAPH_WIDTH - GRAPH_PADDING.left - GRAPH_PADDING.right;
  const plotHeight = GRAPH_HEIGHT - GRAPH_PADDING.top - GRAPH_PADDING.bottom;
  const point = (value, max, index) => {
    const x = GRAPH_PADDING.left + (data.length < 2 ? plotWidth / 2 : index / (data.length - 1) * plotWidth);
    const y = GRAPH_PADDING.top + plotHeight - value / max * plotHeight;
    return `${x},${y}`;
  };
  const collectionPoints = data.map((item, index) => point(item.article_count, collectionMax, index)).join(" ");
  const riskPoints = data.map((item, index) => point(item.risk_event_count, riskMax, index)).join(" ");
  const tickIndexes = [...new Set([0, Math.floor((data.length - 1) / 2), data.length - 1])];

  return <section className="home-trend-graphs" aria-label={`${label} 최근 7일 추세`}>
    <figure className="home-line-graph">
      <figcaption><span className="collection">수집량 <strong>{data.length ? collectionValues.at(-1) : 0}</strong></span><span className="risk">위험 기사 <strong>{data.length ? riskValues.at(-1) : 0}</strong></span></figcaption>
      {data.length ? <svg viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`} role="img" aria-label={`최근 7일 ${label} 수집량과 위험 기사 꺾은선 그래프`}>
        {[0, .5, 1].map((ratio) => <line key={ratio} className="home-graph-grid" x1={GRAPH_PADDING.left} x2={GRAPH_WIDTH - GRAPH_PADDING.right} y1={GRAPH_PADDING.top + plotHeight * ratio} y2={GRAPH_PADDING.top + plotHeight * ratio} />)}
        <polyline className="home-graph-line collection" points={collectionPoints} />
        <polyline className="home-graph-line risk" points={riskPoints} />
        {data.map((item, index) => { const [collectionX, collectionY] = point(item.article_count, collectionMax, index).split(","); const [riskX, riskY] = point(item.risk_event_count, riskMax, index).split(","); return <g key={item.day}><circle className="home-graph-point collection" cx={collectionX} cy={collectionY} r="2.4"><title>{`${item.day} · 수집량 ${item.article_count}건`}</title></circle><circle className="home-graph-point risk" cx={riskX} cy={riskY} r="2.4"><title>{`${item.day} · 위험 기사 ${item.risk_event_count}건`}</title></circle></g>; })}
        {tickIndexes.map((index) => <text className="home-graph-date" key={index} x={GRAPH_PADDING.left + (data.length < 2 ? plotWidth / 2 : index / (data.length - 1) * plotWidth)} y={GRAPH_HEIGHT - 5} textAnchor={index === 0 ? "start" : index === data.length - 1 ? "end" : "middle"}>{data[index].day.slice(5).replace("-", "/")}</text>)}
      </svg> : <p>표시할 추세 데이터가 없습니다.</p>}
    </figure>
  </section>;
}

function CollectedArticles({ articles, label }) {
  return <section className="home-collected-articles" aria-label={`${label} 수집 기사`}>
    <div className="home-content-heading"><span className="eyebrow">COLLECTED ARTICLES</span><h3>수집한 기사</h3></div>
    <div className="home-article-list">
      {articles.length ? articles.slice(0, 5).map((article) => <a className="home-article-row" href={article.url} key={`${article.company_id ?? "company"}-${article.id}`} target="_blank" rel="noreferrer">
        <div><strong>{article.title}</strong><p>{articleSummary(article)}</p></div><small>{SOURCE_LABELS[article.source] ?? article.source} · {formatDate(article.published_at ?? article.created_at)}</small>
      </a>) : <p className="home-article-empty">아직 수집된 기사가 없습니다.</p>}
    </div>
  </section>;
}

function CollectionProgress({ companies }) {
  const total = companies.length;
  const completed = companies.filter((company) => company.readiness_status !== "preparing").length;
  const collecting = companies.filter((company) => ["backfilling", "warming"].includes(company.monitoring_status)).length;
  const percentage = total ? Math.round(completed / total * 100) : 0;

  return <div className="home-collection-progress" aria-label={`수집 준비 완료 ${completed}/${total}${collecting ? `, ${collecting}개 수집 진행 중` : ""}`}>
    <span className="home-collection-label">수집중</span>
    <span className="home-collection-pie" style={{ "--collection-progress": `${percentage * 3.6}deg` }} aria-hidden="true"><i /></span>
    <strong>{completed}/{total}</strong>
    {collecting > 0 && <span className="home-collection-moving"><i aria-hidden="true" />진행 중</span>}
  </div>;
}

function CompanyHeader({ company, role, onOpenCompany, selector, isAll }) {
  const readiness = isAll ? "경쟁사 합산" : READINESS_LABELS[company?.readiness_status] ?? "상태 확인 중";
  return <header className="home-company-header">
    <span className="eyebrow">{role}</span>
    <div className="home-company-meta">
      {selector ?? <h2><button type="button" onClick={() => onOpenCompany(company.id)}>{company.name}</button></h2>}
      <span>{isAll ? "선택한 경쟁사 전체" : company.industry_name}</span>
      <strong>{readiness}</strong>
    </div>
  </header>;
}

// 로그인 직후 메인 기업과 선택한 경쟁사의 최근 기사·위험 추세를 보여 주는 화면이다.
export default function MainPage({ canManageCompanies = true, onOpenCompany, onManageCompanies }) {
  const [companies, setCompanies] = useState([]);
  const [dailySummaries, setDailySummaries] = useState({});
  const [articlesByCompany, setArticlesByCompany] = useState({});
  const [selectedCompetitorId, setSelectedCompetitorId] = useState("all");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const companyResponse = await api.get("/companies");
      const nextCompanies = companyResponse.data;
      setCompanies(nextCompanies);
      const [summaryResults, articleResults] = await Promise.all([
        Promise.allSettled(nextCompanies.map((company) => api.get(`/companies/${company.id}/daily-summaries?days=7`))),
        Promise.allSettled(nextCompanies.map((company) => api.get(`/companies/${company.id}/articles?page=1&page_size=5`))),
      ]);
      setDailySummaries(Object.fromEntries(summaryResults.flatMap((result, index) => result.status === "fulfilled" ? [[nextCompanies[index].id, dailySeries(result.value.data)]] : [])));
      setArticlesByCompany(Object.fromEntries(articleResults.flatMap((result, index) => result.status === "fulfilled" ? [[nextCompanies[index].id, result.value.data.items.map((article) => ({ ...article, company_id: nextCompanies[index].id }))]] : [])));
      setError(null);
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); const timer = window.setInterval(load, 30000); return () => window.clearInterval(timer); }, [load]);

  const mainCompany = companies.find((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");
  const allCompetitorTrend = useMemo(() => Array.from(aggregateDailySeries(competitorCompanies.map((company) => dailySummaries[company.id] ?? [])).values()), [competitorCompanies, dailySummaries]);
  const selectedCompetitor = competitorCompanies.find((company) => String(company.id) === selectedCompetitorId);
  const selectedTrend = selectedCompetitor ? dailySummaries[selectedCompetitor.id] ?? [] : allCompetitorTrend;
  const selectedArticles = useMemo(() => selectedCompetitor ? articlesByCompany[selectedCompetitor.id] ?? [] : competitorCompanies.flatMap((company) => articlesByCompany[company.id] ?? []).sort((left, right) => articleTimestamp(right) - articleTimestamp(left)), [articlesByCompany, competitorCompanies, selectedCompetitor]);

  useEffect(() => {
    if (selectedCompetitorId !== "all" && !selectedCompetitor) setSelectedCompetitorId("all");
  }, [selectedCompetitor, selectedCompetitorId]);

  return <section className="workspace home-workspace">
    {error && <div className="notice error">{error}</div>}
    {loading ? <p className="empty-state">기업 정보를 불러오는 중입니다.</p> : <>
      <div className="home-top-collection"><CollectionProgress companies={companies} /></div>
      {mainCompany ? <section className="home-company-section">
        <CompanyHeader company={mainCompany} role="MAIN COMPANY" onOpenCompany={onOpenCompany} />
        <div className="home-section-body">
          <TrendGraphs data={dailySummaries[mainCompany.id] ?? []} label={mainCompany.name} />
          <CollectedArticles articles={articlesByCompany[mainCompany.id] ?? []} label={mainCompany.name} />
        </div>
      </section> : <p className="empty-state">메인 기업 정보가 없습니다.</p>}
      <section className="home-company-section competitor-section">
        {competitorCompanies.length ? <>
          <CompanyHeader company={selectedCompetitor} role="COMPETITORS" isAll={!selectedCompetitor} onOpenCompany={onOpenCompany} selector={<select className="home-company-selector" aria-label="표시할 경쟁사" value={selectedCompetitorId} onChange={(event) => setSelectedCompetitorId(event.target.value)}><option value="all">전체</option>{competitorCompanies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}</select>} />
          <div className="home-section-body">
            <TrendGraphs data={selectedTrend} label={selectedCompetitor?.name ?? "경쟁사 전체"} />
            <CollectedArticles articles={selectedArticles} label={selectedCompetitor?.name ?? "경쟁사 전체"} />
          </div>
        </> : <div className="empty-state home-empty"><p>아직 등록한 경쟁사가 없습니다.</p>{canManageCompanies && <button type="button" onClick={onManageCompanies}>경쟁사 등록하기</button>}</div>}
      </section>
    </>}
  </section>;
}
