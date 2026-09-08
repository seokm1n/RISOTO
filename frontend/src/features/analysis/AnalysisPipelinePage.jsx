import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import {
  getAnalysisPipelineRiskEventId,
  setAnalysisPipelineRiskEventId,
} from "../../shared/analysisPipelineSession";
import AnalysisPeriodControl from "../../shared/AnalysisPeriodControl";
import { useAnalysisPeriod } from "../../shared/useAnalysisPeriod";
import { Pagination, PanelTitle } from "../../shared/components";
import { resolveSelectedCompany, setSelectedCompanyId as rememberSelectedCompanyId } from "../../shared/selectedCompanySession";
import CompanySearchField from "../../shared/CompanySearchField";
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
import { RiskEventListContent, RiskJudgmentModelInfo } from "./AnalysisStatisticsPage";

const STAGES = [
  { id: "collection", step: "01", label: "15분 수집", kicker: "COLLECTION WINDOWS", description: "15분 단위 수집 품질과 처리량, 최근 실행 이력을 확인합니다." },
  { id: "filtering", step: "02", label: "정제", kicker: "ARTICLE FILTERING", description: "수집 원문의 관련성·광고성·중복 판정과 보류 결과를 확인합니다." },
  { id: "sentiment", step: "03", label: "감성분석", kicker: "SENTIMENT ANALYSIS", description: "정제 기사별 긍정·중립·부정 판정과 기간 분포를 확인합니다." },
  { id: "stories", step: "04", label: "이슈 그룹핑", kicker: "ISSUE GROUPING", description: "같은 사건을 다룬 기사들을 하나의 이슈로 묶습니다." },
  { id: "risk", step: "05", label: "위험판정", kicker: "RISK DETECTION", description: "이슈별 위험도와 유형, 사건 발생 근거를 확인합니다." },
  { id: "response", step: "06", label: "대응", kicker: "RESPONSE MANAGEMENT", description: "위험 이슈의 대응방안을 생성하고 검토·승인 이력을 관리합니다." },
];
const STAGE_IDS = new Set(STAGES.map((stage) => stage.id));
const FILTER_PAGE_SIZE = 5;
const COLLECTION_PAGE_SIZE = 5;
const STORY_PAGE_SIZE = 5;
const SENTIMENT_PAGE_SIZE = 5;
const RISK_EVIDENCE_PAGE_SIZE = 5;
const ARTICLE_FETCH_BATCH_SIZE = 1000;
const RISK_CLASSIFICATIONS = new Set(["risk", "non_risk"]);
const WINDOW_RANGE_OPTIONS = [
  { id: "today", label: "오늘", days: 1 },
  { id: "7d", label: "7일", days: 7 },
  { id: "30d", label: "30일", days: 30 },
];
const WINDOW_QUALITY_FILTERS = [
  { id: "", label: "전체" },
  { id: "completed", label: "완료" },
  { id: "partial", label: "부분·실패" },
];

function DatabaseIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></svg>; }
function FilterIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h16l-6 8v6l-4 2v-8Z" /></svg>; }
function SmileIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01" /></svg>; }
function LayersIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 13 9 5 9-5" /></svg>; }
function AlertIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3 2 20h20L12 3Z" /><path d="M12 10v4M12 17h.01" /></svg>; }
function ClipboardIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4V3h6v1M9 11h6M9 15h6" /></svg>; }
function NewspaperIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 5h13a2 2 0 0 1 2 2v12a2 2 0 0 1-2-2H4Z" /><path d="M4 5v14M8 9h7M8 13h7M8 17h4" /></svg>; }
function GlobeIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18Z" /></svg>; }
function CheckCircleIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="m8.5 12.5 2.5 2.5 5-5.5" /></svg>; }
function RefreshIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 11a8 8 0 0 0-14.9-3.4M4 13a8 8 0 0 0 14.9 3.4" /><path d="M4 4v5h5M20 20v-5h-5" /></svg>; }
function ChevronRightIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6" /></svg>; }
function CalendarIconSmall() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M8 3v4M16 3v4M3 10h18" /></svg>; }

const STAGE_ICONS = {
  collection: DatabaseIcon, filtering: FilterIcon, sentiment: SmileIcon,
  stories: LayersIcon, risk: AlertIcon, response: ClipboardIcon,
};

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

// "7일"/"30일" 탭은 anchor 날짜를 끝으로, days만큼 거슬러 올라간 구간을 만든다.
function seoulDateRangeSpan(anchor, days) {
  const end = new Date(`${anchor}T00:00:00+09:00`);
  end.setUTCDate(end.getUTCDate() + 1);
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

async function fetchAllCompanyArticles(companyId, period, periodBasis = "articles") {
  const periodQuery = `&start_date=${period.start}&end_date=${period.end}&analysis_only=true${periodBasis === "issues" ? "&period_basis=issues&judged_only=true" : ""}`;
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

const WINDOW_QUALITY_LABELS = { complete: "완료", partial: "부분 완료", unavailable: "실패" };

function DeltaBadge({ value, caption }) {
  if (value == null || !Number.isFinite(value)) return <span className="pipeline-quad-delta">{caption}</span>;
  const dir = value > 0 ? "up" : value < 0 ? "down" : "flat";
  const sign = value > 0 ? "+" : value < 0 ? "" : "±";
  return <span className="pipeline-quad-delta"><b className={dir}>{sign}{formatNumber(value)}</b>{caption}</span>;
}

function CollectionStage({
  data, range, onRangeChange, qualityFilter, onQualityFilterChange,
  page, onPageChange, onOpenWindow, onOpenAllHistory,
}) {
  const windows = data.windows ?? [];
  const latest = data.latestWindow ?? windows[0] ?? null;
  const previous = useMemo(() => {
    if (!latest) return null;
    return [...windows]
      .filter((window) => window.id !== latest.id && new Date(window.window_start) < new Date(latest.window_start))
      .sort((left, right) => new Date(right.window_start) - new Date(left.window_start))[0] ?? null;
  }, [windows, latest]);
  const delta = (key) => latest && previous ? (latest[key] ?? 0) - (previous[key] ?? 0) : null;
  const successCount = latest?.successful_sources?.length ?? 0;
  const totalSources = successCount + (latest?.failed_sources?.length ?? 0);
  const qualityDetail = !latest ? "생성 전"
    : `${successCount}/${totalSources || successCount} 성공 · ${formatDate(latest.window_end)} 구간 · ${totalSources === 0 ? "수집원 정보 없음" : successCount === totalSources ? "모든 수집원 성공" : "일부 수집원 실패"}`;

  const filteredWindows = windows.filter((window) => qualityFilter === ""
    || (qualityFilter === "completed" ? window.data_quality === "complete" : window.data_quality !== "complete"));
  const visibleWindows = filteredWindows.slice((page - 1) * COLLECTION_PAGE_SIZE, page * COLLECTION_PAGE_SIZE);

  return <div className="pipeline-stage-content">
    <div className="pipeline-step-head">
      <div>
        <div className="pipeline-step-kicker"><span className="step">STEP 01</span><span className="kind">COLLECTION WINDOWS</span></div>
        <h2>15분 수집</h2>
        <p>서울 시간 :00/:15/:30/:45 구간마다 NAVER·Kakao·Tavily·YouTube에서 원문을 모읍니다. 전체 실패 구간은 0건으로 대체하지 않습니다.</p>
      </div>
      <div className="pipeline-range-tabs" role="tablist" aria-label="조회 범위">
        {WINDOW_RANGE_OPTIONS.map((option) => <button type="button" role="tab" aria-selected={range === option.id} className={range === option.id ? "active" : ""} onClick={() => onRangeChange(option.id)} key={option.id}>{option.label}</button>)}
      </div>
    </div>

    <div className="pipeline-quad-grid">
      <article className="pipeline-quad-card">
        <div className="pipeline-quad-head"><span>최근 구간 기사</span><NewspaperIcon /></div>
        <div className="pipeline-quad-value">{formatNumber(latest?.article_count)}건</div>
        <DeltaBadge value={delta("article_count")} caption="정제 통과 기준" />
      </article>
      <article className="pipeline-quad-card">
        <div className="pipeline-quad-head"><span>최근 구간 스토리</span><LayersIcon /></div>
        <div className="pipeline-quad-value">{formatNumber(latest?.story_count)}건</div>
        <DeltaBadge value={delta("story_count")} caption="중복 보도 통합" />
      </article>
      <article className="pipeline-quad-card">
        <div className="pipeline-quad-head"><span>출처 언론사</span><GlobeIcon /></div>
        <div className="pipeline-quad-value">{formatNumber(latest?.publisher_count)}곳</div>
        <DeltaBadge value={delta("publisher_count")} caption="최근 15분" />
      </article>
      <article className="pipeline-quad-card">
        <div className="pipeline-quad-head"><span>수집 품질</span><CheckCircleIcon /></div>
        <div className="pipeline-quad-value complete">{latest ? WINDOW_QUALITY_LABELS[latest.data_quality] ?? latest.data_quality : "대기"}</div>
        <span className="pipeline-quad-delta">{qualityDetail}</span>
      </article>
    </div>

    <section className="brief-lower-grid">
      <article className="brief-panel">
        <div className="brief-panel-head">
          <div><h3>날짜별 수집 구간</h3><p className="sub">행을 누르면 그 구간에 수집된 기사를 볼 수 있습니다. 위험도 50% 이상은 보라로 표시합니다.</p></div>
        </div>
        <div className="pipeline-table-toolbar">
          <div className="brief-mode-group" role="tablist" aria-label="수집 품질 필터">
            {WINDOW_QUALITY_FILTERS.map((option) => <button type="button" role="tab" aria-selected={qualityFilter === option.id} className={qualityFilter === option.id ? "active" : ""} onClick={() => onQualityFilterChange(option.id)} key={option.id || "all"}>{option.label}</button>)}
          </div>
        </div>
        <div className="pipeline-table-wrap"><table className="pipeline-table"><thead><tr><th>구간</th><th>품질</th><th>기사</th><th>스토리</th><th>확산</th><th>출처</th><th>위험도</th></tr></thead><tbody>{visibleWindows.map((window) => <tr className="pipeline-window-row" tabIndex={0} role="link" aria-label={`${formatDate(window.window_start)} 수집 기사 보기`} onClick={() => onOpenWindow(window)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpenWindow(window); } }} key={window.id}><td>{formatDate(window.window_start)}</td><td><span className={`brief-badge ${window.data_quality === "complete" ? "watch" : window.data_quality === "partial" ? "warning" : "critical"}`}>{WINDOW_QUALITY_LABELS[window.data_quality] ?? window.data_quality}</span></td><td>{window.data_quality === "unavailable" ? "–" : formatNumber(window.article_count)}</td><td>{window.data_quality === "unavailable" ? "–" : formatNumber(window.story_count)}</td><td>{window.data_quality === "unavailable" ? "–" : formatNumber(window.amplification_count)}</td><td>{window.data_quality === "unavailable" ? "–" : formatNumber(window.publisher_count)}</td><td>{window.data_quality === "unavailable" ? <span className="pipeline-window-risk"><span className="pipeline-window-risk-track"><i style={{ width: 0 }} /></span><span>–</span></span> : <span className="pipeline-window-risk"><span className="pipeline-window-risk-track"><i className={(window.risk_probability ?? 0) >= 0.5 ? "high" : ""} style={{ width: `${Math.round((window.risk_probability ?? 0) * 100)}%` }} /></span><span>{formatRiskProbability(window.risk_probability)}</span></span>}</td></tr>)}</tbody></table></div>
        {!filteredWindows.length && <p className="brief-empty-note">{qualityFilter ? "조건에 맞는 수집 구간이 없습니다." : "선택한 날짜에 생성된 15분 수집 구간이 없습니다."}</p>}
        <Pagination page={page} pageSize={COLLECTION_PAGE_SIZE} total={filteredWindows.length} onChange={onPageChange} />
      </article>

      <article className="brief-panel">
        <div className="brief-panel-head"><div><h3>최근 수집 실행 이력</h3><p className="sub">실시간 · 과거 · 수동 수집 작업</p></div></div>
        <div className="pipeline-job-list">{(data.jobs?.items ?? []).map((job) => {
          const dotClass = job.status === "completed" ? "completed" : job.status === "partial" ? "partial" : job.status === "failed" ? "failed" : "running";
          const statusLabel = job.status === "completed" ? "완료" : job.status === "partial" ? "부분 완료" : job.status === "failed" ? "실패" : "진행 중";
          const typeLabel = job.job_type === "realtime" ? "실시간 수집" : job.job_type === "backfill" ? "과거 기사 수집" : "수동 수집";
          return <div className="pipeline-job-row" key={job.id}>
            <span className={`pipeline-job-dot ${dotClass}`} aria-hidden="true" />
            <span className="pipeline-job-title">{typeLabel}<span className={`pipeline-job-badge ${dotClass}`}>{statusLabel}</span></span>
            <span className="pipeline-job-time">{formatDate(job.completed_at ?? job.started_at)}</span>
            <p className="pipeline-job-detail">조회 {formatNumber(job.query_count)} · 수집 {formatNumber(job.fetched_count)} · 신규 {formatNumber(job.new_count)} · 연결 {formatNumber(job.matched_count)}</p>
            <small className="pipeline-job-sources">{(job.sources ?? []).map((source) => SOURCE_LABELS[source] ?? source).join(", ") || "출처 없음"}</small>
          </div>;
        })}</div>
        {!data.jobs?.items?.length && <p className="brief-empty-note">수집 실행 이력이 없습니다.</p>}
        <button type="button" className="pipeline-job-more" onClick={onOpenAllHistory}>수집 관리에서 전체 이력 보기</button>
      </article>
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
      <Stat label="전체 판정" value={`${formatNumber(summary.raw_count)}건`} note="중복 통합 기준" active={filterDecision === ""} onClick={() => onDecisionChange("")} />
      <Stat label="정제 통과" value={`${formatNumber(summary.accepted_count)}건`} note="다음 단계 전달" tone="success" active={filterDecision === "accepted"} onClick={() => onDecisionChange("accepted")} />
      <Stat label="제외" value={`${formatNumber(summary.rejected_count)}건`} note={`중복 ${formatNumber(summary.duplicate_count)}건`} active={filterDecision === "rejected"} onClick={() => onDecisionChange("rejected")} />
      <Stat label="검토 필요" value={`${formatNumber(summary.review_required_count)}건`} note="관련성·광고성 모호" tone="warning" active={filterDecision === "review_required"} onClick={() => onDecisionChange("review_required")} />
    </div>
    <section className="panel pipeline-panel">
      <PanelTitle kicker="FILTER RESULTS" title={resultTitle} />
      {filterDecision === "review_required" && reviewNotice && <div className={`notice ${reviewNotice.type} filter-review-notice`} role="status">{reviewNotice.message}</div>}
      <div className="pipeline-result-list">{(data.filterResults?.items ?? []).map((item) => {
        const decisionLabel = item.decision === "accepted" ? "통과" : item.decision === "review_required" ? "검토 필요" : "제외";
        const details = <><p>관련성 {formatScore(item.relevance_score)} · 광고성 {formatScore(item.advertising_score)} · 신뢰도 {formatScore(item.confidence)}</p><small>{FILTER_REASON_LABELS[item.reason] ?? item.reason} · {SOURCE_LABELS[item.source] ?? item.source} · {formatDate(item.article_date ?? item.published_at ?? item.collected_at ?? item.filtered_at)}</small></>;
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
      <div className="pipeline-panel-heading story-group-heading"><div className="story-view-tabs" role="tablist" aria-label="스토리 기사 수 구분"><button type="button" role="tab" aria-selected={storyView === "multi"} className={storyView === "multi" ? "active" : ""} onClick={() => { setStoryView("multi"); setStoryPage(1); }}>기사 2건 이상 <strong>{formatNumber(multiArticleGroups.length)}</strong></button><button type="button" role="tab" aria-selected={storyView === "single"} className={storyView === "single" ? "active" : ""} onClick={() => { setStoryView("single"); setStoryPage(1); }}>기사 1건 <strong>{formatNumber(singleArticleGroups.length)}</strong></button></div></div>
      <div className="story-cluster-list">{pageGroups.map((group) => <article key={group.id}><details><summary><strong>{group.articles[0]?.title}</strong><small>기사 {formatNumber(group.articles.length)}건</small><small className="story-group-latest">최근 {formatDate(group.latest)}</small></summary><div>{group.articles.map((article) => <a href={article.url} target="_blank" rel="noreferrer" key={article.id}><span>{SOURCE_LABELS[article.source] ?? article.source}</span><p>{article.title}</p><small>{formatDate(article.published_at ?? article.created_at)}</small></a>)}</div><footer>기사 {formatNumber(group.articles.length)}건 · 출처 {formatNumber(group.sources.size)}곳</footer></details></article>)}</div>
      {!visibleGroups.length && <p className="panel-empty">{storyView === "single" ? "기사 1건으로만 구성된 스토리가 없습니다." : "기사 2건 이상인 스토리가 없습니다."}</p>}
      <Pagination page={visibleStoryPage} pageSize={STORY_PAGE_SIZE} total={visibleGroups.length} onChange={setStoryPage} />
    </section>
  </div>;
}

function SentimentStage({ data }) {
  const [sentimentPage, setSentimentPage] = useState(1);
  const [sentimentFilter, setSentimentFilter] = useState("all");
  const articles = data.articles?.items ?? [];
  const counts = articles.reduce((result, article) => { const kind = sentimentKind(article.sentiment_label); result[kind] = (result[kind] ?? 0) + 1; return result; }, { positive: 0, neutral: 0, negative: 0, pending: 0 });
  const analyzed = counts.positive + counts.neutral + counts.negative;
  const filteredArticles = articles.filter((article) => {
    const kind = sentimentKind(article.sentiment_label);
    return sentimentFilter === "all" || kind === sentimentFilter;
  });
  const sentimentPages = Math.max(1, Math.ceil(filteredArticles.length / SENTIMENT_PAGE_SIZE));
  const visibleSentimentPage = Math.min(sentimentPage, sentimentPages);
  const visibleArticles = filteredArticles.slice(
    (visibleSentimentPage - 1) * SENTIMENT_PAGE_SIZE,
    visibleSentimentPage * SENTIMENT_PAGE_SIZE,
  );
  const listTitle = sentimentFilter === "all" ? "분석 대상 기사" : `${sentimentText(sentimentFilter)} 기사`;
  const distributionTitle = "선택 기간 기사 감성 분포";
  const selectSentiment = (kind) => { setSentimentFilter(kind); setSentimentPage(1); };
  return <div className="pipeline-stage-content">
    <section className="panel pipeline-panel"><div className="pipeline-panel-heading"><PanelTitle kicker="SENTIMENT COMPOSITION" title={distributionTitle} /></div><div className="sentiment-distribution">{["positive", "neutral", "negative"].map((kind) => <div key={kind}><span>{sentimentText(kind)}</span><i><b className={kind} style={{ width: `${analyzed ? counts[kind] / analyzed * 100 : 0}%` }} /></i><strong>{formatPercent(analyzed ? counts[kind] / analyzed : null)} · {formatNumber(counts[kind])}건</strong></div>)}</div></section>
    <div className="pipeline-stat-grid" aria-label="기사 감성 목록 선택"><Stat label="분석 대상" value={`${formatNumber(articles.length)}건`} note={`완료 ${formatNumber(analyzed)}건 · 대기 ${formatNumber(counts.pending)}건`} active={sentimentFilter === "all"} onClick={() => selectSentiment("all")} /><Stat label="긍정" value={`${formatNumber(counts.positive)}건`} note={formatPercent(analyzed ? counts.positive / analyzed : null)} tone="success" active={sentimentFilter === "positive"} onClick={() => selectSentiment("positive")} /><Stat label="중립" value={`${formatNumber(counts.neutral)}건`} note={formatPercent(analyzed ? counts.neutral / analyzed : null)} active={sentimentFilter === "neutral"} onClick={() => selectSentiment("neutral")} /><Stat label="부정" value={`${formatNumber(counts.negative)}건`} note={formatPercent(analyzed ? counts.negative / analyzed : null)} tone="danger" active={sentimentFilter === "negative"} onClick={() => selectSentiment("negative")} /></div>
    <section className="panel pipeline-panel"><PanelTitle kicker="ANALYZED ARTICLES" title={listTitle} /><div className="pipeline-result-list">{visibleArticles.map((article) => <a className="pipeline-result-row linked" href={article.url} target="_blank" rel="noreferrer" key={article.id}><div><span className={`sentiment-pill ${sentimentKind(article.sentiment_label)}`}>{sentimentText(article.sentiment_label)}</span><strong>{article.title}</strong></div><p>긍정 {formatPercent(article.positive_probability)} · 중립 {formatPercent(article.neutral_probability)} · 부정 {formatPercent(article.negative_probability)}</p><small>{SOURCE_LABELS[article.source] ?? article.source} · 신뢰도 {formatPercent(article.sentiment_confidence)} · {formatDate(article.published_at ?? article.created_at)}</small></a>)}</div>{!filteredArticles.length && <p className="panel-empty">선택한 감성의 분석 결과가 없습니다.</p>}<Pagination page={visibleSentimentPage} pageSize={SENTIMENT_PAGE_SIZE} total={filteredArticles.length} onChange={setSentimentPage} /></section>
  </div>;
}

function RecentCollectionDate({ risk }) {
  const latest = (risk.evidence_articles ?? []).reduce((current, article) => {
    const value = article.collected_at;
    return value && (!current || new Date(value) > new Date(current)) ? value : current;
  }, null);
  return <small className="pipeline-risk-collected-date">최근 수집 {formatDate(latest)}</small>;
}

function RiskStage({ data, selectedRiskId, classification, onSelect, onClassificationChange, onOpenResponse }) {
  const events = data.risks?.items ?? [];
  const selected = events.find((risk) => risk.id === selectedRiskId) ?? events[0] ?? null;
  const [evidencePage, setEvidencePage] = useState(1);
  const [evidenceSort, setEvidenceSort] = useState("time");
  const [listOpen, setListOpen] = useState(false);
  const dropdownRef = useRef(null);
  const evidenceArticles = useMemo(() => {
    const articles = selected?.evidence_articles ?? [];
    const collectedTime = (article) => Date.parse(article.collected_at) || 0;
    const riskScore = (article) => article.risk_probability != null && Number.isFinite(Number(article.risk_probability))
      ? Number(article.risk_probability)
      : -1;
    return [...articles].sort((left, right) => {
      const riskDifference = evidenceSort === "risk" ? riskScore(right) - riskScore(left) : 0;
      return riskDifference || collectedTime(right) - collectedTime(left)
        || String(left.article_id).localeCompare(String(right.article_id), undefined, { numeric: true });
    });
  }, [selected?.evidence_articles, evidenceSort]);
  const evidencePageCount = Math.max(1, Math.ceil(evidenceArticles.length / RISK_EVIDENCE_PAGE_SIZE));
  const visibleEvidencePage = Math.min(evidencePage, evidencePageCount);
  const visibleEvidenceArticles = evidenceArticles.slice(
    (visibleEvidencePage - 1) * RISK_EVIDENCE_PAGE_SIZE,
    visibleEvidencePage * RISK_EVIDENCE_PAGE_SIZE,
  );
  useEffect(() => { setEvidencePage(1); }, [selected?.id, classification]);
  useEffect(() => { setListOpen(false); }, [classification]);
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
  const classificationLabel = isRisk ? "위험" : "비위험";
  const summary = data.risks?.summary ?? {};
  useEffect(() => {
    if (!isRisk || !selected || selected.id === selectedRiskId) return;
    onSelect(selected.id);
  }, [isRisk, onSelect, selected, selectedRiskId]);
  const listTitle = `${classificationLabel} 이슈 선택`;
  const emptyMessage = `선택 기간의 판정 대상 중 ${classificationLabel} 이슈가 없습니다.`;
  return <div className="pipeline-stage-content">
    <div className="pipeline-stat-grid risk-stage-stat-grid">
      <Stat label="위험 이슈" value={`${formatNumber(summary.risk)}건`} note="위험으로 판정" tone="danger" active={isRisk} onClick={() => onClassificationChange("risk")} />
      <Stat label="비위험 이슈" value={`${formatNumber(summary.non_risk)}건`} note="비위험으로 판정" tone="success" active={classification === "non_risk"} onClick={() => onClassificationChange("non_risk")} />
    </div>
    <section className="panel pipeline-panel pipeline-risk-picker-panel">
      <div className="pipeline-panel-heading pipeline-risk-list-heading"><PanelTitle title={listTitle} /></div>
      {selected ? <div className={`pipeline-risk-dropdown${listOpen ? " open" : ""}`} ref={dropdownRef}>
        <button className="pipeline-risk-dropdown-trigger risk-event-list-item selected" type="button" aria-expanded={listOpen} aria-controls="risk-judgment-event-list" onClick={() => setListOpen((open) => !open)}>
          <div className="pipeline-risk-dropdown-value"><RiskEventListContent risk={selected} judgmentCompact /></div>
          <div className="pipeline-risk-dropdown-meta"><RecentCollectionDate risk={selected} /><span className="pipeline-risk-dropdown-action">{listOpen ? "목록 접기" : "목록 펼치기"}<i aria-hidden="true" /></span></div>
        </button>
        {listOpen && <div className="pipeline-risk-dropdown-menu risk-list selectable" id="risk-judgment-event-list" aria-label={`${classificationLabel} 판정 목록`}>{events.map((risk) => <button className={`risk-event-list-item ${selected.id === risk.id ? "selected" : ""}`} type="button" aria-pressed={selected.id === risk.id} onClick={() => { setListOpen(false); onSelect(risk.id); }} key={`${classification}-${risk.id}`}><div className="pipeline-risk-dropdown-value"><RiskEventListContent risk={risk} judgmentCompact /></div><RecentCollectionDate risk={risk} /></button>)}</div>}
      </div> : <p className="panel-empty">{emptyMessage}</p>}
    </section>
    <section className="panel pipeline-panel pipeline-risk-evidence">
      <div className="pipeline-panel-heading pipeline-risk-detail-heading"><PanelTitle kicker="DETECTION DETAIL" title="판정 상세" />{selected && isRisk && <button className="secondary-button" type="button" onClick={() => onOpenResponse(selected)}>대응 보기</button>}</div>
      {selected ? <>
        <div className="pipeline-risk-head"><div><span className={isRisk ? `severity ${selected.severity}` : "judgment-badge non-risk"}>{isRisk ? selected.severity === "critical" ? "긴급" : "주의" : classificationLabel}</span><h3>{riskEventTitle(selected)}</h3><RiskJudgmentModelInfo risk={selected} /></div></div>
        <div className="pipeline-risk-metrics"><div><span>위험도</span><strong>{formatRiskProbability(selected.risk_probability)}</strong></div><div><span>위험 판정 기사</span><strong>{formatNumber(selected.risk_article_count ?? 0)}건</strong></div><div><span>관련 보도</span><strong>{formatNumber(selected.evidence_article_count ?? evidenceArticles.length)}건</strong></div><div><span>출처</span><strong>{formatNumber(selected.source_count ?? selected.risk_source_count ?? 0)}곳</strong></div></div>
        {isRisk && !!selected.risk_types?.length && <div className="risk-type-list">{selected.risk_types.map((type) => <span className={type.is_primary ? "primary" : ""} key={type.risk_type}>{RISK_TYPE_LABELS[type.risk_type] ?? type.risk_type} {formatPercent(type.probability)}</span>)}</div>}
        <div className="pipeline-evidence-toolbar">
          <h4 className="pipeline-evidence-heading">근거 기사</h4>
          <select aria-label="근거 기사 정렬" value={evidenceSort} onChange={(event) => { setEvidenceSort(event.target.value); setEvidencePage(1); }}>
            <option value="time">시간순</option>
            <option value="risk">위험도순</option>
          </select>
        </div>
        <div className="pipeline-evidence-list">{visibleEvidenceArticles.map((article) => <a href={article.url} target="_blank" rel="noreferrer" key={article.article_id}><span>{isRisk ? article.evidence_role === "trigger" ? "위험 판정" : "관련 보도" : "판정 기사"}</span><strong>{article.title}</strong><small>{article.source_domain || article.source || "출처 미상"} · 근거 점수 {formatPercent(article.evidence_score)} · 수집 {formatDate(article.collected_at)}</small></a>)}</div>
        <Pagination page={visibleEvidencePage} pageSize={RISK_EVIDENCE_PAGE_SIZE} total={evidenceArticles.length} onChange={setEvidencePage} />
      </> : <p className="panel-empty">확인할 판정 결과를 선택해 주세요.</p>}
    </section>
  </div>;
}

// 6단계 각각의 "오늘" 수치를 한 화면에서 보여주고, 카드를 누르면 그 단계로
// 이동한다. 사이드바 대신 이 요약이 단계 사이를 오가는 진입점 역할을 한다.
function PipelineFlowOverview({ overview, overviewError, stageId, onSelectStage }) {
  const monitoring = overview?.monitoring;
  const daily = overview?.daily;
  const filterSummary = overview?.filterSummary;
  const riskSummary = overview?.riskSummary;
  const responseSummary = overview?.responseSummary;

  const counts = {
    collection: daily?.article_count ?? 0,
    filtering: filterSummary?.accepted_count ?? 0,
    sentiment: daily?.negative_article_count ?? 0,
    stories: daily?.story_count ?? 0,
    risk: riskSummary?.risk ?? 0,
    response: responseSummary?.active ?? 0,
  };
  const cards = [
    { id: "collection", step: "01", label: "15분 수집", value: counts.collection, note: "원문 수집" },
    { id: "filtering", step: "02", label: "정제", value: counts.filtering, note: filterSummary ? `통과 · 제외 ${formatNumber((filterSummary.rejected_count ?? 0) + (filterSummary.duplicate_count ?? 0))} · 검토 ${formatNumber(filterSummary.review_required_count ?? 0)}` : "통과" },
    { id: "sentiment", step: "03", label: "감성분석", value: counts.sentiment, note: daily ? `부정 · 긍정 ${formatNumber(daily.positive_article_count)} · 중립 ${formatNumber(daily.neutral_article_count)}` : "부정" },
    { id: "stories", step: "04", label: "이슈 그룹핑", value: counts.stories, note: daily ? `이슈 · 기사 ${formatNumber(daily.article_count)}건 묶음` : "이슈" },
    { id: "risk", step: "05", label: "위험판정", value: counts.risk, note: "오늘 위험 사건 판정" },
    { id: "response", step: "06", label: "대응", value: counts.response, note: responseSummary ? `활성 위험 · 검토 필요 ${formatNumber(responseSummary.needs_response ?? 0)}건` : "초안 생성" },
  ];
  const maxCount = Math.max(1, counts.collection);
  const modelLabel = monitoring?.model_version
    ? `${monitoring.model_version}${monitoring.model_state === "provisional" ? " · provisional" : ""}`
    : "모델 정보 확인 중";

  return <section className="brief-panel pipeline-flow-panel">
    <div className="pipeline-flow-head">
      <div>
        <h2>오늘의 파이프라인 흐름</h2>
        <p>{seoulDateValue().slice(5).replace("-", ".")} 기준. 수집된 원문이 6단계를 거쳐 위험 사건과 대응 초안으로 좁혀집니다. 단계를 누르면 아래 내용이 바뀝니다.</p>
      </div>
      <span className="pipeline-model-chip">{modelLabel}</span>
    </div>
    {overviewError && <div className="notice error">{overviewError}</div>}
    <div className="pipeline-flow-cards">
      {cards.map((card) => {
        const Icon = STAGE_ICONS[card.id];
        return <button type="button" className={`pipeline-flow-card${stageId === card.id ? " active" : ""}`} onClick={() => onSelectStage(card.id)} key={card.id}>
          <div className="pipeline-flow-card-head"><span>{card.step} {card.label}</span><Icon /></div>
          <strong className="flow-value">{formatNumber(card.value)}</strong>
          <small>{card.note}</small>
        </button>;
      })}
    </div>
    <div className="pipeline-flow-bar" aria-hidden="true">
      {cards.map((card) => <div className="pipeline-flow-bar-seg" style={{ width: `${Math.max(4, Math.round(card.value / maxCount * 100))}%` }} key={card.id} />)}
      <span className="pipeline-flow-bar-arrow">{cards.map((card) => formatNumber(card.value)).join(" → ")}</span>
    </div>
  </section>;
}

export default function AnalysisPipelinePage() {
  const { stage: requestedStage } = useParams();
  const stageId = STAGE_IDS.has(requestedStage) ? requestedStage : "collection";
  const stage = STAGES.find((item) => item.id === stageId);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [companies, setCompanies] = useState([]);
  const [companiesLoading, setCompaniesLoading] = useState(true);
  const [data, setData] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filterDecision, setFilterDecision] = useState("");
  const { period, changePeriod } = useAnalysisPeriod();
  const periodKey = `${period.start}:${period.end}`;
  const periodQuery = `start_date=${period.start}&end_date=${period.end}`;
  const [loadedKey, setLoadedKey] = useState(null);
  const [page, setPage] = useState(1);
  const [collectionDate, setCollectionDate] = useState(() => seoulDateValue());
  const [windowRange, setWindowRange] = useState("today");
  const [qualityFilter, setQualityFilter] = useState("");
  const [articleWindow, setArticleWindow] = useState(null);
  const [overview, setOverview] = useState(null);
  const [overviewError, setOverviewError] = useState(null);
  const [manualCollecting, setManualCollecting] = useState(false);
  const requestSequence = useRef(0);
  const selectedCompanyId = searchParams.get("companyId") ?? "";
  const requestedRiskClassification = searchParams.get("classification") ?? "risk";
  const riskClassification = RISK_CLASSIFICATIONS.has(requestedRiskClassification) ? requestedRiskClassification : "risk";
  const querySelectedRiskId = stageId === "risk" && requestedRiskClassification !== riskClassification
    ? null : Number(searchParams.get("eventId")) || null;
  const rememberedRiskId = getAnalysisPipelineRiskEventId(selectedCompanyId);
  const selectedRiskId = querySelectedRiskId ?? (riskClassification === "risk" ? rememberedRiskId : null);
  const viewKey = `${stageId}:${selectedCompanyId}:${stageId === "collection" ? `${collectionDate}:${windowRange}` : periodKey}:${filterDecision}:${riskClassification}:${page}`;
  const currentViewKey = useRef(viewKey);
  currentViewKey.current = viewKey;
  const visibleData = loadedKey === viewKey ? data : {};

  useEffect(() => {
    if (stageId !== "risk" || requestedRiskClassification === riskClassification) return;
    setSearchParams((current) => {
      const params = new URLSearchParams(current);
      params.set("classification", riskClassification);
      params.delete("eventId");
      return params;
    }, { replace: true });
  }, [requestedRiskClassification, riskClassification, setSearchParams, stageId]);

  useEffect(() => {
    const isRiskSelection = stageId === "response"
      || (stageId === "risk" && riskClassification === "risk");
    if (isRiskSelection && selectedCompanyId && querySelectedRiskId) {
      setAnalysisPipelineRiskEventId(selectedCompanyId, querySelectedRiskId);
    }
  }, [querySelectedRiskId, riskClassification, selectedCompanyId, stageId]);

  useEffect(() => {
    let active = true;
    setCompaniesLoading(true);
    api.get("/companies").then((response) => {
      if (!active) return;
      const next = response.data ?? [];
      setCompanies(next);
      const selected = resolveSelectedCompany(next, selectedCompanyId);
      if (selected) rememberSelectedCompanyId(selected.id);
      if (selected && String(selected.id) !== selectedCompanyId) setSearchParams((current) => { const params = new URLSearchParams(current); params.set("companyId", String(selected.id)); return params; }, { replace: true });
    }).catch((requestError) => active && setError(getErrorMessage(requestError)))
      .finally(() => { if (active) setCompaniesLoading(false); });
    return () => { active = false; };
  }, [selectedCompanyId, setSearchParams]);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (viewKey !== currentViewKey.current) return;
    if (!selectedCompanyId) { setLoading(false); return; }
    const requestId = ++requestSequence.current;
    if (!silent) { setLoading(true); setError(null); }
    try {
      let result;
      if (stageId === "response") {
        setData({}); setError(null); setLoading(false);
        return;
      }
      if (stageId === "collection") {
        const spanDays = WINDOW_RANGE_OPTIONS.find((option) => option.id === windowRange)?.days ?? 1;
        const range = spanDays === 1 ? seoulDateRange(collectionDate) : seoulDateRangeSpan(collectionDate, spanDays);
        const [monitoring, latestWindows, windows, jobs] = await Promise.all([
          api.get(`/companies/${selectedCompanyId}/monitoring`),
          api.get(`/companies/${selectedCompanyId}/feature-windows?limit=1`),
          // 서버가 limit을 2000으로 제한한다(30일 * 96구간 = 2880보다 작음). 30일
          // 범위에서는 가장 최근 2000구간(약 20일 분량)까지만 보인다.
          api.get(`/companies/${selectedCompanyId}/feature-windows?date_from=${encodeURIComponent(range.start)}&date_to=${encodeURIComponent(range.end)}&limit=2000`),
          api.get(`/companies/${selectedCompanyId}/collection-jobs?page=1&page_size=5`),
        ]);
        result = { monitoring: monitoring.data, latestWindow: latestWindows.data?.[0] ?? null, windows: windows.data, jobs: jobs.data };
      } else if (stageId === "filtering") {
        const decision = filterDecision ? `&decision=${filterDecision}` : "";
        const [summary, results] = await Promise.all([api.get(`/companies/${selectedCompanyId}/filter-summary?${periodQuery}&analysis_only=true`), api.get(`/companies/${selectedCompanyId}/filter-results?page=${page}&page_size=${FILTER_PAGE_SIZE}${decision}&${periodQuery}&analysis_only=true`)]);
        result = { filterSummary: summary.data, filterResults: results.data };
      } else if (stageId === "stories") {
        result = { articles: await fetchAllCompanyArticles(selectedCompanyId, period, "issues") };
      } else if (stageId === "sentiment") {
        result = { articles: await fetchAllCompanyArticles(selectedCompanyId, period) };
      } else {
        const params = new URLSearchParams({ classification: riskClassification, view: "all", page: "1", page_size: "100" });
        params.set("start_date", period.start);
        params.set("end_date", period.end);
        const risks = await api.get(`/companies/${selectedCompanyId}/risk-judgments/page?${params}`);
        const items = [...(risks.data?.items ?? [])];
        const totalPages = Math.ceil((risks.data?.total ?? 0) / 100);
        for (let riskPage = 2; riskPage <= totalPages; riskPage += 1) {
          if (requestId !== requestSequence.current || viewKey !== currentViewKey.current) return;
          params.set("page", String(riskPage));
          const next = await api.get(`/companies/${selectedCompanyId}/risk-judgments/page?${params}`);
          items.push(...(next.data?.items ?? []));
        }
        result = { risks: { ...risks.data, items } };
      }
      if (requestId !== requestSequence.current || viewKey !== currentViewKey.current) return;
      setData(result); setLoadedKey(viewKey); setError(null);
    } catch (requestError) { if (requestId === requestSequence.current && viewKey === currentViewKey.current) setError(getErrorMessage(requestError)); }
    finally { if (requestId === requestSequence.current && viewKey === currentViewKey.current) setLoading(false); }
  }, [collectionDate, filterDecision, page, period, periodQuery, riskClassification, selectedCompanyId, stageId, viewKey, windowRange]);

  useEffect(() => { load(); const timer = window.setInterval(() => load({ silent: true }), 30000); return () => { window.clearInterval(timer); requestSequence.current += 1; }; }, [load]);
  useEffect(() => { setPage(1); }, [selectedCompanyId, stageId, periodKey, windowRange, qualityFilter]);

  // 오늘의 파이프라인 흐름 요약은 어느 단계를 보고 있든 항상 "오늘" 기준으로
  // 6단계 전부를 한 번에 보여준다. 선택한 단계의 조회 범위와는 독립적이다.
  useEffect(() => {
    if (!selectedCompanyId) { setOverview(null); return; }
    let active = true;
    const today = seoulDateValue();
    const loadOverview = () => Promise.all([
      api.get(`/companies/${selectedCompanyId}/monitoring`),
      api.get(`/companies/${selectedCompanyId}/daily-summaries?start_date=${today}&end_date=${today}`),
      api.get(`/companies/${selectedCompanyId}/filter-summary?start_date=${today}&end_date=${today}&analysis_only=true`),
      api.get(`/companies/${selectedCompanyId}/risk-judgments/page?classification=risk&view=all&page=1&page_size=1&start_date=${today}&end_date=${today}`),
      // 대응 단계는 "오늘 새로 열린 것"이 아니라 지금 활성 상태인 전체를 본다.
      // 여기만 날짜로 좁히면 위험 사건이 어제 이전에 열렸다는 이유로 0건처럼
      // 보여 대응이 밀려 있는데도 없는 것처럼 오해하게 된다.
      api.get(`/companies/${selectedCompanyId}/risk-events/page?view=active&page=1&page_size=1&response=all`),
    ]).then(([monitoring, daily, filterSummary, riskJudgments, riskEvents]) => {
      if (!active) return;
      setOverview({
        monitoring: monitoring.data,
        daily: daily.data?.[0] ?? null,
        filterSummary: filterSummary.data,
        riskSummary: riskJudgments.data?.summary ?? null,
        responseSummary: riskEvents.data?.summary ?? null,
      });
      setOverviewError(null);
    }).catch((requestError) => { if (active) setOverviewError(getErrorMessage(requestError)); });
    loadOverview();
    const timer = window.setInterval(loadOverview, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [selectedCompanyId]);

  const updateRiskSelection = useCallback((eventId) => {
    if (eventId && riskClassification === "risk") {
      setAnalysisPipelineRiskEventId(selectedCompanyId, eventId);
    }
    setSearchParams((current) => { const params = new URLSearchParams(current); if (eventId) params.set("eventId", String(eventId)); else params.delete("eventId"); return params; });
  }, [riskClassification, selectedCompanyId, setSearchParams]);
  const updateRiskQuery = (changes) => {
    setSearchParams((current) => { const params = new URLSearchParams(current); Object.entries(changes).forEach(([key, value]) => { if (value == null || value === "") params.delete(key); else params.set(key, String(value)); }); params.delete("eventId"); return params; });
  };
  const updateRiskClassification = (classification) => {
    if (classification !== riskClassification) updateRiskQuery({ classification, view: null, risk_type: null, severity: null, response: null });
  };
  const openRiskResponse = (risk) => {
    setAnalysisPipelineRiskEventId(selectedCompanyId, risk.id);
    const responseView = risk.status !== "closed" && ["idle", "deferred", "failed"].includes(risk.response_generation_status) ? "needs_response" : "all";
    const params = new URLSearchParams({ companyId: String(selectedCompanyId), eventId: String(risk.id), view: responseView });
    navigate(`/analysis/response?${params}`);
  };
  const selectCompany = (companyId) => { rememberSelectedCompanyId(companyId); setSearchParams({ companyId }); };
  const triggerManualCollection = async () => {
    if (!selectedCompanyId || manualCollecting) return;
    setManualCollecting(true);
    try {
      // 본문이 필수라 빈 객체라도 보내야 한다 - 모든 필드에 기본값이 있어도
      // 요청 본문 자체가 없으면 FastAPI가 422로 거부한다.
      // 실제 네이버·카카오·Tavily·YouTube에 순서대로 질의하는 작업이라 기본
      // 타임아웃(15초)보다 오래 걸린다 - 정제 재검토 호출과 같은 시간을 준다.
      await api.post(`/companies/${selectedCompanyId}/collect`, {}, { timeout: 120000 });
      await load({ silent: true });
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setManualCollecting(false); }
  };
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
  const monitoringStatus = overview?.monitoring?.monitoring_status;
  const monitoringLive = monitoringStatus && !["paused", "archived", "error"].includes(monitoringStatus);
  const lastCollectedLabel = overview?.monitoring?.last_collected_at
    ? new Date(overview.monitoring.last_collected_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }).replace(/^(오전|오후)\s*/, "")
    : null;

  return <main className="workspace analysis-statistics-workspace analysis-pipeline-workspace signal-scope brief-dashboard">
    <p className="brief-crumb">Pipeline <ChevronRightIcon /> <b>{selectedCompany ? `${selectedCompany.name} · ${selectedCompany.company_role === "main" ? "나의 기업" : "비교 기업"}` : "기업 미선택"}</b></p>

    <div className="brief-head-row">
      <h1>{selectedCompany ? `${selectedCompany.name} 분석 파이프라인` : "분석 파이프라인"}
        {overview && <span className={`pipeline-live-pill${monitoringLive ? "" : " paused"}`}><i aria-hidden="true" />{monitoringLive ? "실시간 탐지중" : "탐지 중지"}{lastCollectedLabel && ` · 마지막 수집 ${lastCollectedLabel}`}</span>}
      </h1>
      <div className="brief-head-controls">
        <CompanySearchField companies={companies} selectedCompany={selectedCompany} onSelect={selectCompany} />
        <label className="brief-date-pill"><CalendarIconSmall /><input type="date" value={collectionDate} max={seoulDateValue()} onChange={(event) => { if (event.target.value) { setCollectionDate(event.target.value); setPage(1); } }} /></label>
        <button type="button" className={`brief-cta ghost${manualCollecting ? " spin" : ""}`} onClick={triggerManualCollection} disabled={!selectedCompanyId || manualCollecting}><RefreshIcon />{manualCollecting ? "수집 중…" : "수동 수집"}</button>
      </div>
    </div>

    {selectedCompanyId && <PipelineFlowOverview overview={overview} overviewError={overviewError} stageId={stageId} onSelectStage={moveStage} />}

    {stageId !== "collection" && <div className="pipeline-step-head">
      <div>
        <div className="pipeline-step-kicker"><span className="step">STEP {stage.step}</span>{stage.kicker && <span className="kind">{stage.kicker}</span>}</div>
        <h2>{stage.label}</h2>
        <p>{stage.description}</p>
      </div>
      <AnalysisPeriodControl period={period} onChange={(field, value) => { setPage(1); changePeriod(field, value); }} />
    </div>}
    {error && <div className="notice error">{error}</div>}
    {companiesLoading || (companies.length > 0 && selectedCompanyId && stageId !== "response" && !error && (loading || loadedKey !== viewKey) && !Object.keys(visibleData).length) ? <p className="empty-state">{stage.label} 데이터를 불러오는 중입니다.</p> : !companies.length ? <p className="empty-state">먼저 기업을 등록해 주세요.</p> : <>
      {stageId === "collection" && <CollectionStage data={visibleData} range={windowRange} onRangeChange={setWindowRange} qualityFilter={qualityFilter} onQualityFilterChange={setQualityFilter} page={page} onPageChange={setPage} onOpenWindow={setArticleWindow} onOpenAllHistory={() => navigate(`/manage?section=collection${selectedCompanyId ? `&articleCompanyId=${selectedCompanyId}` : ""}`)} />}
      {stageId === "filtering" && <FilteringStage key={`${selectedCompanyId}:${periodKey}`} companyId={selectedCompanyId} data={visibleData} filterDecision={filterDecision} onDecisionChange={(value) => { setFilterDecision(value); setPage(1); }} page={page} onPageChange={setPage} onRefresh={() => load({ silent: true })} />}
      {stageId === "stories" && <StoryStage key={`${selectedCompanyId}:${periodKey}`} data={visibleData} />}
      {stageId === "sentiment" && <SentimentStage key={`${selectedCompanyId}:${periodKey}`} data={visibleData} />}
      {stageId === "risk" && <RiskStage key={`${selectedCompanyId}:${periodKey}`} data={visibleData} selectedRiskId={selectedRiskId} classification={riskClassification} onSelect={updateRiskSelection} onClassificationChange={updateRiskClassification} onOpenResponse={openRiskResponse} />}
      {stageId === "response" && <RiskManagementPage dateRange={period} canReview initialCompanyId={selectedCompanyId} initialRiskEventId={rememberedRiskId} embedded />}
    </>}
    {articleWindow && selectedCompany && <CollectedArticlesDialog company={selectedCompany} windowRange={{ start: articleWindow.window_start, end: articleWindow.window_end }} onClose={() => setArticleWindow(null)} />}
  </main>;
}
