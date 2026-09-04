import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import {
  getAnalysisPipelineRiskEventId,
  setAnalysisPipelineRiskEventId,
} from "../../shared/analysisPipelineSession";
import { Pagination, PanelTitle } from "../../shared/components";
import { resolveSelectedCompany, setSelectedCompanyId as rememberSelectedCompanyId } from "../../shared/selectedCompanySession";
import { CollectedArticlesDialog } from "../collection/CollectionPage";
import RiskManagementPage from "../risk-management/RiskManagementPage";
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
  { id: "sentiment", step: "03", label: "감성분석", kicker: "SENTIMENT ANALYSIS", description: "정제 기사별 긍정·중립·부정 판정과 기간 분포를 확인합니다." },
  { id: "stories", step: "04", label: "스토리 군집화", kicker: "STORY CLUSTERING", description: "여러 출처의 유사 기사가 어떤 하나의 스토리로 묶였는지 확인합니다." },
  { id: "risk", step: "05", label: "위험판정", kicker: "RISK DETECTION", description: "스토리별 위험도와 유형, 사건 발생 근거를 확인합니다." },
  { id: "response", step: "06", label: "대응", kicker: "RESPONSE MANAGEMENT", description: "위험 사건의 대응방안을 생성하고 검토·승인 이력을 관리합니다." },
];
const STAGE_IDS = new Set(STAGES.map((stage) => stage.id));
const FILTER_PAGE_SIZE = 5;
const COLLECTION_PAGE_SIZE = 5;
const STORY_PAGE_SIZE = 5;
const SENTIMENT_PAGE_SIZE = 5;
const RISK_EVIDENCE_PAGE_SIZE = 5;
const ARTICLE_FETCH_BATCH_SIZE = 1000;
const RISK_PERIOD_OPTIONS = [
  { value: "all", label: "전체" },
  { value: "1", label: "1일" },
  { value: "3", label: "3일" },
  { value: "7", label: "7일" },
];
const RISK_PERIODS = new Set(RISK_PERIOD_OPTIONS.map((option) => option.value));
const RISK_CLASSIFICATIONS = new Set(["risk", "non_risk"]);

function seoulDateValue(value = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const get = (type) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function seoulDateRange(value) {
  const start = new Date(`${value}T00:00:00+09:00`);
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

async function fetchAllCompanyArticles(companyId, period) {
  const periodQuery = period === "all" ? "" : `&days=${encodeURIComponent(period)}`;
  const firstResponse = await api.get(`/companies/${companyId}/articles?page=1&page_size=${ARTICLE_FETCH_BATCH_SIZE}${periodQuery}`);
  const firstPage = firstResponse.data ?? {};
  const items = [...(firstPage.items ?? [])];
  const total = Math.max(Number(firstPage.total) || 0, items.length);
  const totalPages = Math.ceil(total / ARTICLE_FETCH_BATCH_SIZE);

  for (let articlePage = 2; articlePage <= totalPages; articlePage += 1) {
    const response = await api.get(`/companies/${companyId}/articles?page=${articlePage}&page_size=${ARTICLE_FETCH_BATCH_SIZE}${periodQuery}`);
    items.push(...(response.data?.items ?? []));
  }

  return { ...firstPage, items, total, page: 1, page_size: items.length };
}

const safeNumber = (value) => Math.max(Number(value) || 0, 0);
function Stat({ label, value, note, tone = "", active = false, onClick }) {
  const className = `pipeline-stat ${tone}${onClick ? " selectable" : ""}${active ? " active" : ""}`;
  const content = <><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</>;
  return onClick
    ? <button className={className} type="button" aria-pressed={active} onClick={onClick}>{content}</button>
    : <article className={className}>{content}</article>;
}

function CollectionStage({ data, date, page, onDateChange, onPageChange, onOpenWindow }) {
  const latest = data.latestWindow ?? data.windows?.[0];
  const windows = data.windows ?? [];
  const visibleWindows = windows.slice(
    (page - 1) * COLLECTION_PAGE_SIZE,
    page * COLLECTION_PAGE_SIZE,
  );
  return <div className="pipeline-stage-content">
    <div className="pipeline-stat-grid">
      <Stat label="최근 구간 기사" value={`${formatNumber(latest?.article_count)}건`} note="정제 통과" />
      <Stat label="최근 구간 스토리" value={`${formatNumber(latest?.story_count)}건`} note="중복 보도 통합" />
      <Stat label="출처" value={`${formatNumber(latest?.publisher_count)}곳`} note="최근 15분" />
      <Stat label="수집 품질" value={latest ? DATA_QUALITY_LABELS[latest.data_quality] : "대기"} note={latest ? formatDate(latest.window_end) : "생성 전"} tone={latest?.data_quality ?? ""} />
    </div>
    <section className="panel pipeline-panel">
      <div className="pipeline-panel-heading collection-window-heading"><PanelTitle kicker="15-MINUTE WINDOWS" title="날짜별 수집 구간" /><label><span>조회 날짜</span><input type="date" value={date} max={seoulDateValue()} onChange={(event) => onDateChange(event.target.value)} /></label></div>
      <div className="pipeline-table-wrap"><table className="pipeline-table"><thead><tr><th>구간</th><th>품질</th><th>기사</th><th>스토리</th><th>확산</th><th>출처</th><th>위험도</th></tr></thead><tbody>{visibleWindows.map((window) => <tr className="pipeline-window-row" tabIndex={0} role="link" aria-label={`${formatDate(window.window_start)} 수집 기사 보기`} onClick={() => onOpenWindow(window)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpenWindow(window); } }} key={window.id}><td>{formatDate(window.window_start)}</td><td><span className={`quality-pill ${window.data_quality}`}>{DATA_QUALITY_LABELS[window.data_quality]}</span></td><td>{formatNumber(window.article_count)}</td><td>{formatNumber(window.story_count)}</td><td>{formatNumber(window.amplification_count)}</td><td>{formatNumber(window.publisher_count)}</td><td>{formatRiskProbability(window.risk_probability)}</td></tr>)}</tbody></table></div>
      {!windows.length && <p className="panel-empty">선택한 날짜에 생성된 15분 수집 구간이 없습니다.</p>}
      <Pagination page={page} pageSize={COLLECTION_PAGE_SIZE} total={windows.length} onChange={onPageChange} />
    </section>
    <section className="panel pipeline-panel">
      <PanelTitle kicker="COLLECTION JOBS" title="최근 수집 실행 이력" />
      <div className="pipeline-result-list">{(data.jobs?.items ?? []).map((job) => <article className="pipeline-result-row" key={job.id}><div><span className={`pipeline-status ${job.status}`}>{job.status === "completed" ? "완료" : job.status === "partial" ? "부분 완료" : job.status === "failed" ? "실패" : "진행 중"}</span><strong>{job.job_type === "realtime" ? "실시간 수집" : job.job_type === "backfill" ? "과거 기사 수집" : "수동 수집"}</strong></div><p>조회 {formatNumber(job.query_count)}회 · 수집 {formatNumber(job.fetched_count)}건 · 신규 {formatNumber(job.new_count)}건 · 연결 {formatNumber(job.matched_count)}건</p><small>{(job.sources ?? []).map((source) => SOURCE_LABELS[source] ?? source).join(", ")} · {formatDate(job.completed_at ?? job.started_at)}</small></article>)}</div>
      {!data.jobs?.items?.length && <p className="panel-empty">수집 실행 이력이 없습니다.</p>}
    </section>
  </div>;
}

function FilteringStage({ companyId, data, filterDecision, onDecisionChange, page, onPageChange, onRefresh }) {
  const summary = data.filterSummary ?? {};
  const [reviewingId, setReviewingId] = useState(null);
  const [reviewNotice, setReviewNotice] = useState(null);
  useEffect(() => { setReviewNotice(null); }, [companyId, filterDecision]);
  const resultTitle = filterDecision === "accepted"
    ? "정제 통과 목록"
    : filterDecision === "rejected"
      ? "제외 목록"
      : filterDecision === "review_required"
        ? "검토 필요 목록"
        : "전체 판정 결과";
  const reviewWithLlm = async (item) => {
    if (reviewingId !== null) return;
    setReviewingId(item.id);
    setReviewNotice(null);
    try {
      const response = await api.post(
        `/companies/${companyId}/filter-results/${item.id}/llm-review`,
        undefined,
        { timeout: 120000 },
      );
      const decisionLabel = response.data.decision === "accepted" ? "통과" : "제외";
      setReviewNotice({
        type: "success",
        message: `LLM 재검토 결과: ${decisionLabel} · ${response.data.explanation}`,
      });
      if ((data.filterResults?.items?.length ?? 0) === 1 && page > 1) onPageChange(page - 1);
      else await onRefresh();
    } catch (requestError) {
      setReviewNotice({ type: "error", message: getErrorMessage(requestError) });
    } finally {
      setReviewingId(null);
    }
  };
  return <div className="pipeline-stage-content">
    <div className="pipeline-stat-grid" aria-label="정제 판정 목록 선택">
      <Stat label="전체 판정" value={`${formatNumber(summary.raw_count)}건`} note="최신 필터 버전" active={filterDecision === ""} onClick={() => onDecisionChange("")} />
      <Stat label="정제 통과" value={`${formatNumber(summary.accepted_count)}건`} note="다음 단계 전달" tone="success" active={filterDecision === "accepted"} onClick={() => onDecisionChange("accepted")} />
      <Stat label="제외" value={`${formatNumber(summary.rejected_count)}건`} note={`중복 ${formatNumber(summary.duplicate_count)}건`} active={filterDecision === "rejected"} onClick={() => onDecisionChange("rejected")} />
      <Stat label="검토 필요" value={`${formatNumber(summary.review_required_count)}건`} note="관련성·광고성 모호" tone="warning" active={filterDecision === "review_required"} onClick={() => onDecisionChange("review_required")} />
    </div>
    <section className="panel pipeline-panel">
      <PanelTitle kicker="FILTER RESULTS" title={resultTitle} />
      {filterDecision === "review_required" && reviewNotice && <div className={`notice ${reviewNotice.type} filter-review-notice`} role="status">{reviewNotice.message}</div>}
      <div className="pipeline-result-list">{(data.filterResults?.items ?? []).map((item) => {
        const decisionLabel = item.decision === "accepted" ? "통과" : item.decision === "review_required" ? "검토 필요" : "제외";
        const details = <><p>관련성 {formatScore(item.relevance_score)} · 광고성 {formatScore(item.advertising_score)} · 신뢰도 {formatScore(item.confidence)}</p><small>{FILTER_REASON_LABELS[item.reason] ?? item.reason} · {SOURCE_LABELS[item.source] ?? item.source} · {formatDate(item.filtered_at)}</small></>;
        if (filterDecision !== "review_required") return <a className="pipeline-result-row linked" href={item.url} target="_blank" rel="noreferrer" key={item.id}><div><span className={`filter-pill ${item.decision}`}>{decisionLabel}</span><strong>{item.title}</strong></div>{details}</a>;
        const isReviewing = reviewingId === item.id;
        return <article className="pipeline-result-row filter-review-row" key={item.id}>
          <div><span className={`filter-pill ${item.decision}`}>{decisionLabel}</span><a className="filter-result-title-link" href={item.url} target="_blank" rel="noreferrer"><strong>{item.title}</strong></a><button className="filter-rereview-button" type="button" disabled={reviewingId !== null} aria-label={`${item.title} LLM 재검토`} onClick={() => reviewWithLlm(item)}>{isReviewing ? "LLM 검토 중…" : "LLM 재검토"}</button></div>
          {details}
        </article>;
      })}</div>
      {!data.filterResults?.items?.length && <p className="panel-empty">선택한 조건의 정제 결과가 없습니다.</p>}
      <Pagination page={page} pageSize={FILTER_PAGE_SIZE} total={data.filterResults?.total ?? 0} onChange={onPageChange} />
    </section>
  </div>;
}

function StoryStage({ data }) {
  const [storyView, setStoryView] = useState("multi");
  const [storyPage, setStoryPage] = useState(1);
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
  const multiArticleGroups = groups.filter((group) => group.articles.length >= 2);
  const singleArticleGroups = groups.filter((group) => group.articles.length === 1);
  const visibleGroups = storyView === "single" ? singleArticleGroups : multiArticleGroups;
  const storyPages = Math.max(1, Math.ceil(visibleGroups.length / STORY_PAGE_SIZE));
  const visibleStoryPage = Math.min(storyPage, storyPages);
  const pageGroups = visibleGroups.slice(
    (visibleStoryPage - 1) * STORY_PAGE_SIZE,
    visibleStoryPage * STORY_PAGE_SIZE,
  );
  return <div className="pipeline-stage-content">
    <section className="panel pipeline-panel">
      <div className="pipeline-panel-heading"><PanelTitle kicker="STORY GROUPS" title={storyView === "multi" ? "기사 2건 이상" : "기사 1건"} /><div className="story-view-tabs" role="tablist" aria-label="스토리 기사 수 구분"><button type="button" role="tab" aria-selected={storyView === "multi"} className={storyView === "multi" ? "active" : ""} onClick={() => { setStoryView("multi"); setStoryPage(1); }}>기사 2건 이상 <strong>{formatNumber(multiArticleGroups.length)}</strong></button><button type="button" role="tab" aria-selected={storyView === "single"} className={storyView === "single" ? "active" : ""} onClick={() => { setStoryView("single"); setStoryPage(1); }}>기사 1건 <strong>{formatNumber(singleArticleGroups.length)}</strong></button></div></div>
      <p className="story-view-note">같은 출처의 기사끼리여도 괜찮으며, 기사 수가 2건 이상인 스토리만 위험 사건 판정 대상으로 전달됩니다.</p>
      <div className="story-cluster-list">{pageGroups.map((group) => <article key={group.id}><details><summary><span>STORY #{String(group.id).replace("article-", "")}</span><strong>{group.articles[0]?.title}</strong><small>기사 {formatNumber(group.articles.length)}건</small></summary><div>{group.articles.map((article) => <a href={article.url} target="_blank" rel="noreferrer" key={article.id}><span>{SOURCE_LABELS[article.source] ?? article.source}</span><p>{article.title}</p><small>{formatDate(article.published_at ?? article.created_at)}</small></a>)}</div><footer>기사 {formatNumber(group.articles.length)}건 · 출처 {formatNumber(group.sources.size)}곳 · 최근 {formatDate(group.latest)}</footer></details></article>)}</div>
      {!visibleGroups.length && <p className="panel-empty">{storyView === "single" ? "기사 1건으로만 구성된 스토리가 없습니다." : "기사 2건 이상인 스토리가 없습니다."}</p>}
      <Pagination page={visibleStoryPage} pageSize={STORY_PAGE_SIZE} total={visibleGroups.length} onChange={setStoryPage} />
    </section>
  </div>;
}

function SentimentStage({ data, period, onPeriodChange }) {
  const [sentimentPage, setSentimentPage] = useState(1);
  const [sentimentFilter, setSentimentFilter] = useState("analyzed");
  const articles = data.articles?.items ?? [];
  const counts = articles.reduce((result, article) => { const kind = sentimentKind(article.sentiment_label); result[kind] = (result[kind] ?? 0) + 1; return result; }, { positive: 0, neutral: 0, negative: 0, pending: 0 });
  const analyzed = counts.positive + counts.neutral + counts.negative;
  const filteredArticles = articles.filter((article) => {
    const kind = sentimentKind(article.sentiment_label);
    return sentimentFilter === "analyzed"
      ? ["positive", "neutral", "negative"].includes(kind)
      : kind === sentimentFilter;
  });
  const sentimentPages = Math.max(1, Math.ceil(filteredArticles.length / SENTIMENT_PAGE_SIZE));
  const visibleSentimentPage = Math.min(sentimentPage, sentimentPages);
  const visibleArticles = filteredArticles.slice(
    (visibleSentimentPage - 1) * SENTIMENT_PAGE_SIZE,
    visibleSentimentPage * SENTIMENT_PAGE_SIZE,
  );
  const listTitle = sentimentFilter === "analyzed" ? "분석 완료 기사" : `${sentimentText(sentimentFilter)} 기사`;
  const distributionTitle = period === "all" ? "전체 기사 감성 분포" : `최근 ${period}일 기사 감성 분포`;
  const selectSentiment = (kind) => { setSentimentFilter(kind); setSentimentPage(1); };
  return <div className="pipeline-stage-content">
    <section className="panel pipeline-panel"><div className="pipeline-panel-heading"><PanelTitle kicker="SENTIMENT COMPOSITION" title={distributionTitle} /><label><span>조회 기간</span><select value={period} onChange={(event) => { setSentimentPage(1); onPeriodChange(event.target.value); }}><option value="all">전체</option><option value="7">7일</option><option value="1">1일</option></select></label></div><div className="sentiment-distribution">{["positive", "neutral", "negative"].map((kind) => <div key={kind}><span>{sentimentText(kind)}</span><i><b className={kind} style={{ width: `${analyzed ? counts[kind] / analyzed * 100 : 0}%` }} /></i><strong>{formatPercent(analyzed ? counts[kind] / analyzed : null)} · {formatNumber(counts[kind])}건</strong></div>)}</div></section>
    <div className="pipeline-stat-grid" aria-label="기사 감성 목록 선택"><Stat label="분석 완료" value={`${formatNumber(analyzed)}건`} note="긍정·중립·부정" active={sentimentFilter === "analyzed"} onClick={() => selectSentiment("analyzed")} /><Stat label="긍정" value={`${formatNumber(counts.positive)}건`} note={formatPercent(analyzed ? counts.positive / analyzed : null)} tone="success" active={sentimentFilter === "positive"} onClick={() => selectSentiment("positive")} /><Stat label="중립" value={`${formatNumber(counts.neutral)}건`} note={formatPercent(analyzed ? counts.neutral / analyzed : null)} active={sentimentFilter === "neutral"} onClick={() => selectSentiment("neutral")} /><Stat label="부정" value={`${formatNumber(counts.negative)}건`} note={formatPercent(analyzed ? counts.negative / analyzed : null)} tone="danger" active={sentimentFilter === "negative"} onClick={() => selectSentiment("negative")} /></div>
    <section className="panel pipeline-panel"><PanelTitle kicker="ANALYZED ARTICLES" title={listTitle} /><div className="pipeline-result-list">{visibleArticles.map((article) => <a className="pipeline-result-row linked" href={article.url} target="_blank" rel="noreferrer" key={article.id}><div><span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span><strong>{article.title}</strong></div><p>긍정 {formatPercent(article.positive_probability)} · 중립 {formatPercent(article.neutral_probability)} · 부정 {formatPercent(article.negative_probability)}</p><small>{SOURCE_LABELS[article.source] ?? article.source} · 신뢰도 {formatPercent(article.sentiment_confidence)} · {formatDate(article.published_at ?? article.created_at)}</small></a>)}</div>{!filteredArticles.length && <p className="panel-empty">선택한 감성의 분석 결과가 없습니다.</p>}<Pagination page={visibleSentimentPage} pageSize={SENTIMENT_PAGE_SIZE} total={filteredArticles.length} onChange={setSentimentPage} /></section>
  </div>;
}

function RiskStage({ data, selectedRiskId, classification, period, onSelect, onClassificationChange, onFilterChange, onOpenResponse }) {
  const events = data.risks?.items ?? [];
  const selected = events.find((risk) => risk.id === selectedRiskId) ?? events[0] ?? null;
  const [evidencePage, setEvidencePage] = useState(1);
  const [listOpen, setListOpen] = useState(false);
  const dropdownRef = useRef(null);
  const evidenceArticles = selected?.evidence_articles ?? [];
  const evidencePageCount = Math.max(1, Math.ceil(evidenceArticles.length / RISK_EVIDENCE_PAGE_SIZE));
  const visibleEvidencePage = Math.min(evidencePage, evidencePageCount);
  const visibleEvidenceArticles = evidenceArticles.slice(
    (visibleEvidencePage - 1) * RISK_EVIDENCE_PAGE_SIZE,
    visibleEvidencePage * RISK_EVIDENCE_PAGE_SIZE,
  );
  useEffect(() => { setEvidencePage(1); }, [selected?.id]);
  useEffect(() => { setListOpen(false); }, [classification, period]);
  useEffect(() => {
    if (!listOpen) return undefined;
    const closeOutside = (event) => {
      if (!dropdownRef.current?.contains(event.target)) setListOpen(false);
    };
    const closeWithEscape = (event) => {
      if (event.key === "Escape") setListOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeWithEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeWithEscape);
    };
  }, [listOpen]);
  const isRisk = classification === "risk";
  useEffect(() => {
    if (!isRisk || !selected || selected.id === selectedRiskId) return;
    onSelect(selected.id);
  }, [isRisk, onSelect, selected, selectedRiskId]);
  const listTitle = isRisk ? "위험 사건 선택" : "비위험 사건 선택";
  const emptyMessage = `선택한 기간에 마지막 관련 기사가 추가된 ${isRisk ? "위험 사건" : "비위험 스토리"}이 없습니다.`;
  return <div className="pipeline-stage-content">
    <div className="pipeline-stat-grid risk-stage-stat-grid"><Stat label="위험" value={`${formatNumber(data.risks?.summary?.risk)}건`} note="위험 사건 판정" tone="danger" active={isRisk} onClick={() => onClassificationChange("risk")} /><Stat label="비위험" value={`${formatNumber(data.risks?.summary?.non_risk)}건`} note="비위험 스토리 판정" tone="success" active={!isRisk} onClick={() => onClassificationChange("non_risk")} /></div>
    <section className="panel pipeline-panel pipeline-risk-picker-panel">
      <div className="pipeline-panel-heading pipeline-risk-list-heading"><PanelTitle title={listTitle} /><div className="pipeline-risk-list-controls"><select aria-label="조회 기간" value={period} onChange={(event) => onFilterChange({ days: event.target.value })}>{RISK_PERIOD_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></div></div>
      {selected ? <div className={`pipeline-risk-dropdown${listOpen ? " open" : ""}`} ref={dropdownRef}>
        <button className="pipeline-risk-dropdown-trigger risk-event-list-item selected" type="button" aria-expanded={listOpen} aria-controls="risk-judgment-event-list" onClick={() => setListOpen((open) => !open)}>
          <div className="pipeline-risk-dropdown-value"><RiskEventListContent risk={selected} judgmentCompact /></div>
          <span className="pipeline-risk-dropdown-action">{listOpen ? "목록 접기" : "목록 펼치기"}<i aria-hidden="true" /></span>
        </button>
        {listOpen && <div className="pipeline-risk-dropdown-menu risk-list selectable" id="risk-judgment-event-list" aria-label={`${isRisk ? "위험" : "비위험"} 판정 목록`}>{events.map((risk) => <button className={`risk-event-list-item ${selected.id === risk.id ? "selected" : ""}`} type="button" aria-pressed={selected.id === risk.id} onClick={() => { setListOpen(false); onSelect(risk.id); }} key={`${classification}-${risk.id}`}><RiskEventListContent risk={risk} judgmentCompact /></button>)}</div>}
      </div> : <p className="panel-empty">{emptyMessage}</p>}
    </section>
    <section className="panel pipeline-panel pipeline-risk-evidence"><div className="pipeline-panel-heading pipeline-risk-detail-heading"><PanelTitle kicker="DETECTION DETAIL" title="판정 상세" />{selected && isRisk && <button className="secondary-button" type="button" onClick={() => onOpenResponse(selected)}>대응 보기</button>}</div>{selected ? <><div className="pipeline-risk-head"><div><span className={isRisk ? `severity ${selected.severity}` : "judgment-badge non-risk"}>{isRisk ? selected.severity === "critical" ? "긴급" : "주의" : "비위험"}</span><h3>{riskEventTitle(selected)}</h3></div></div><div className="pipeline-risk-metrics"><div><span>위험도</span><strong>{formatRiskProbability(selected.risk_probability)}</strong></div><div><span>위험 판정 기사</span><strong>{formatNumber(selected.risk_article_count ?? 0)}건</strong></div><div><span>관련 보도</span><strong>{formatNumber(selected.evidence_article_count ?? evidenceArticles.length)}건</strong></div><div><span>출처</span><strong>{formatNumber(selected.source_count ?? selected.risk_source_count ?? 0)}곳</strong></div></div>{isRisk && !!selected.risk_types?.length && <div className="risk-type-list">{selected.risk_types.map((type) => <span className={type.is_primary ? "primary" : ""} key={type.risk_type}>{RISK_TYPE_LABELS[type.risk_type] ?? type.risk_type} {formatPercent(type.probability)}</span>)}</div>}<div className="pipeline-evidence-list">{visibleEvidenceArticles.map((article) => <a href={article.url} target="_blank" rel="noreferrer" key={article.article_id}><span>{isRisk ? article.evidence_role === "trigger" ? "위험 판정" : "관련 보도" : "판정 기사"}</span><strong>{article.title}</strong><small>{article.source_domain || article.source || "출처 미상"} · 근거 점수 {formatPercent(article.evidence_score)}</small></a>)}</div><Pagination page={visibleEvidencePage} pageSize={RISK_EVIDENCE_PAGE_SIZE} total={evidenceArticles.length} onChange={setEvidencePage} /></> : <p className="panel-empty">확인할 판정 결과를 선택해 주세요.</p>}</section>
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
  const [sentimentPeriod, setSentimentPeriod] = useState("all");
  const [page, setPage] = useState(1);
  const [collectionDate, setCollectionDate] = useState(() => seoulDateValue());
  const [articleWindow, setArticleWindow] = useState(null);
  const requestSequence = useRef(0);
  const selectedCompanyId = searchParams.get("companyId") ?? "";
  const querySelectedRiskId = Number(searchParams.get("eventId")) || null;
  const rememberedRiskId = getAnalysisPipelineRiskEventId(selectedCompanyId);
  const selectedRiskId = querySelectedRiskId ?? rememberedRiskId;
  const requestedRiskClassification = searchParams.get("classification") ?? "risk";
  const riskClassification = RISK_CLASSIFICATIONS.has(requestedRiskClassification) ? requestedRiskClassification : "risk";
  const requestedRiskPeriod = searchParams.get("days") ?? "all";
  const riskPeriod = RISK_PERIODS.has(requestedRiskPeriod) ? requestedRiskPeriod : "all";

  useEffect(() => {
    const isRiskSelection = stageId === "response"
      || (stageId === "risk" && riskClassification === "risk");
    if (isRiskSelection && selectedCompanyId && querySelectedRiskId) {
      setAnalysisPipelineRiskEventId(selectedCompanyId, querySelectedRiskId);
    }
  }, [querySelectedRiskId, riskClassification, selectedCompanyId, stageId]);

  useEffect(() => {
    let active = true;
    api.get("/companies").then((response) => {
      if (!active) return;
      const next = response.data ?? [];
      setCompanies(next);
      const selected = resolveSelectedCompany(next, selectedCompanyId);
      if (selected) rememberSelectedCompanyId(selected.id);
      if (selected && String(selected.id) !== selectedCompanyId) setSearchParams((current) => { const params = new URLSearchParams(current); params.set("companyId", String(selected.id)); return params; }, { replace: true });
    }).catch((requestError) => active && setError(getErrorMessage(requestError)));
    return () => { active = false; };
  }, [selectedCompanyId, setSearchParams]);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!selectedCompanyId) { setLoading(false); return; }
    const requestId = ++requestSequence.current;
    if (!silent) setLoading(true);
    try {
      let result;
      if (stageId === "response") {
        setData({}); setError(null); setLoading(false);
        return;
      }
      if (stageId === "collection") {
        const range = seoulDateRange(collectionDate);
        const [monitoring, latestWindows, windows, jobs] = await Promise.all([
          api.get(`/companies/${selectedCompanyId}/monitoring`),
          api.get(`/companies/${selectedCompanyId}/feature-windows?limit=1`),
          api.get(`/companies/${selectedCompanyId}/feature-windows?date_from=${encodeURIComponent(range.start)}&date_to=${encodeURIComponent(range.end)}&limit=2000`),
          api.get(`/companies/${selectedCompanyId}/collection-jobs?page=1&page_size=5`),
        ]);
        result = { monitoring: monitoring.data, latestWindow: latestWindows.data?.[0] ?? null, windows: windows.data, jobs: jobs.data };
      } else if (stageId === "filtering") {
        const decision = filterDecision ? `&decision=${filterDecision}` : "";
        const [summary, results] = await Promise.all([api.get(`/companies/${selectedCompanyId}/filter-summary`), api.get(`/companies/${selectedCompanyId}/filter-results?page=${page}&page_size=${FILTER_PAGE_SIZE}${decision}`)]);
        result = { filterSummary: summary.data, filterResults: results.data };
      } else if (stageId === "stories") {
        result = { articles: await fetchAllCompanyArticles(selectedCompanyId, "all") };
      } else if (stageId === "sentiment") {
        result = { articles: await fetchAllCompanyArticles(selectedCompanyId, sentimentPeriod) };
      } else {
        const params = new URLSearchParams({ classification: riskClassification, view: "all", page: "1", page_size: "100" });
        if (riskPeriod !== "all") params.set("days", riskPeriod);
        const risks = await api.get(`/companies/${selectedCompanyId}/risk-judgments/page?${params}`);
        result = { risks: risks.data };
      }
      if (requestId !== requestSequence.current) return;
      setData(result); setError(null);
    } catch (requestError) { if (requestId === requestSequence.current) setError(getErrorMessage(requestError)); }
    finally { if (requestId === requestSequence.current) setLoading(false); }
  }, [collectionDate, filterDecision, page, riskClassification, riskPeriod, selectedCompanyId, sentimentPeriod, stageId]);

  useEffect(() => { load(); const timer = window.setInterval(() => load({ silent: true }), 30000); return () => { window.clearInterval(timer); requestSequence.current += 1; }; }, [load]);
  useEffect(() => { setPage(1); setData({}); }, [selectedCompanyId, stageId]);

  const updateRiskSelection = useCallback((eventId) => {
    if (eventId && riskClassification === "risk") {
      setAnalysisPipelineRiskEventId(selectedCompanyId, eventId);
    }
    setSearchParams((current) => { const params = new URLSearchParams(current); if (eventId) params.set("eventId", String(eventId)); else params.delete("eventId"); return params; });
  }, [riskClassification, selectedCompanyId, setSearchParams]);
  const updateRiskQuery = (changes) => {
    setData({});
    setSearchParams((current) => { const params = new URLSearchParams(current); Object.entries(changes).forEach(([key, value]) => { if (value == null || value === "") params.delete(key); else params.set(key, String(value)); }); params.delete("eventId"); return params; });
  };
  const updateRiskClassification = (classification) => updateRiskQuery({ classification, view: null, risk_type: null, severity: null, response: null });
  const openRiskResponse = (risk) => {
    setAnalysisPipelineRiskEventId(selectedCompanyId, risk.id);
    const responseView = risk.status !== "closed" && ["idle", "deferred", "failed"].includes(risk.response_generation_status) ? "needs_response" : "all";
    const params = new URLSearchParams({ companyId: String(selectedCompanyId), eventId: String(risk.id), view: responseView });
    params.set("days", "all");
    navigate(`/analysis/response?${params}`);
  };
  const selectCompany = (companyId) => { rememberSelectedCompanyId(companyId); setSearchParams({ companyId }); };
  const moveStage = (nextStage) => {
    const params = new URLSearchParams();
    if (selectedCompanyId) params.set("companyId", selectedCompanyId);
    const riskEventId = getAnalysisPipelineRiskEventId(selectedCompanyId);
    if (["risk", "response"].includes(nextStage) && riskEventId) {
      params.set("eventId", String(riskEventId));
    }
    navigate(`/analysis/${nextStage}${params.size ? `?${params}` : ""}`);
  };
  const mainCompanies = companies.filter((company) => company.company_role === "main");
  const competitorCompanies = companies.filter((company) => company.company_role === "competitor");
  const selectedCompany = companies.find((company) => String(company.id) === selectedCompanyId) ?? null;

  return <section className="analysis-pipeline-shell">
    <aside className="analysis-pipeline-sidebar"><div><span className="eyebrow">ANALYSIS PIPELINE</span><h2>분석 파이프라인</h2><p>수집부터 위험판정과 대응까지 단계별 결과를 확인합니다.</p></div><nav aria-label="분석 파이프라인">{STAGES.map((item, index) => <button type="button" className={stageId === item.id ? "active" : ""} aria-current={stageId === item.id ? "page" : undefined} onClick={() => moveStage(item.id)} key={item.id}><span>{item.step}</span><strong>{item.label}</strong>{index < STAGES.length - 1 && <i aria-hidden="true" />}</button>)}</nav></aside>
    <main className="workspace analysis-statistics-workspace analysis-pipeline-workspace">
      <header className="pipeline-heading"><div><span className="eyebrow">{stage.kicker}</span><h1>{stage.label}</h1><p>{stage.description}</p></div><label><span>분석 기업</span><select value={selectedCompanyId} onChange={(event) => selectCompany(event.target.value)}><option value="" disabled>기업을 선택하세요</option>{mainCompanies.length > 0 && <optgroup label="나의 기업">{mainCompanies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}</optgroup>}{competitorCompanies.length > 0 && <optgroup label="비교 기업">{competitorCompanies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}</optgroup>}</select></label></header>
      {error && <div className="notice error">{error}</div>}
      {stageId !== "response" && loading && !Object.keys(data).length ? <p className="empty-state">{stage.label} 데이터를 불러오는 중입니다.</p> : !companies.length ? <p className="empty-state">먼저 기업을 등록해 주세요.</p> : <>
        {stageId === "collection" && <CollectionStage data={data} date={collectionDate} page={page} onDateChange={(value) => { if (!value) return; setCollectionDate(value); setPage(1); }} onPageChange={setPage} onOpenWindow={setArticleWindow} />}
        {stageId === "filtering" && <FilteringStage companyId={selectedCompanyId} data={data} filterDecision={filterDecision} onDecisionChange={(value) => { setFilterDecision(value); setPage(1); }} page={page} onPageChange={setPage} onRefresh={() => load({ silent: true })} />}
        {stageId === "stories" && <StoryStage key={selectedCompanyId} data={data} />}
        {stageId === "sentiment" && <SentimentStage key={selectedCompanyId} data={data} period={sentimentPeriod} onPeriodChange={(value) => { setSentimentPeriod(value); setData({}); }} />}
        {stageId === "risk" && <RiskStage data={data} selectedRiskId={selectedRiskId} classification={riskClassification} period={riskPeriod} onSelect={updateRiskSelection} onClassificationChange={updateRiskClassification} onFilterChange={updateRiskQuery} onOpenResponse={openRiskResponse} />}
        {stageId === "response" && <RiskManagementPage canReview initialCompanyId={selectedCompanyId} initialRiskEventId={rememberedRiskId} embedded />}
      </>}
    </main>
    {articleWindow && selectedCompany && <CollectedArticlesDialog company={selectedCompany} windowRange={{ start: articleWindow.window_start, end: articleWindow.window_end }} onClose={() => setArticleWindow(null)} />}
  </section>;
}
