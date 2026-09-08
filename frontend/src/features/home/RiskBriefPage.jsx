import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import MainResponseContent, {
  BulletText,
  HOLD_STATUS,
  STANCE_LABELS,
  STRATEGY_LABELS,
  TIER_LABELS,
  TIER_TONES,
  bandsOf,
  deadlineLabel,
  humanize,
  summaryRows,
} from "../analysis/MainResponseContent";
import PeerRecommendationContent from "../analysis/PeerRecommendationContent";
import {
  RISK_TYPE_LABELS,
  formatDate,
  formatNumber,
  formatPercent,
  formatRiskProbability,
  riskEventTitle,
} from "../../shared/presentation";

const RESPONSE_STATUS_LABELS = {
  pending: "생성 중", generating: "생성 중", generated: "생성 완료",
  deferred: "보류", failed: "생성 실패", idle: "미생성",
};
const EVIDENCE_VISIBLE_COUNT = 4;
const PAGE_SIZE = 100;
const MAX_PAGES = 6;

function severityBandOf(risk) {
  return risk?.severity === "critical" ? "critical" : risk?.severity === "warning" ? "warning" : "watch";
}
const SEVERITY_LABEL = { critical: "긴급", warning: "주의", watch: "관찰" };

// 기업의 위험 이벤트를 전부 훑어 id로 찾는다. 단건 조회 API가 없어 목록 API를 그대로 쓴다.
async function findRiskEvent(companyId, eventId) {
  for (let page = 1; page <= MAX_PAGES; page += 1) {
    const params = new URLSearchParams({ view: "all", page: String(page), page_size: String(PAGE_SIZE), response: "all" });
    const response = await api.get(`/companies/${companyId}/risk-events/page?${params}`);
    const items = response.data?.items ?? [];
    const found = items.find((item) => String(item.id) === String(eventId));
    if (found) return found;
    if (items.length < PAGE_SIZE) break;
  }
  return null;
}

function EvidenceList({ risk }) {
  const [expanded, setExpanded] = useState(false);
  const articles = [...(risk.evidence_articles ?? [])].sort(
    (left, right) => new Date(right.collected_at ?? 0) - new Date(left.collected_at ?? 0),
  );
  if (!articles.length) return <p className="brief-empty-note">연결된 근거 기사가 없습니다.</p>;
  const visible = expanded ? articles : articles.slice(0, EVIDENCE_VISIBLE_COUNT);
  return <>
    <div className="brief-evidence-list">
      {visible.map((article) => <div className="brief-evidence-item" key={article.article_id}>
        <span className="role">{article.evidence_role === "trigger" ? "판정" : "관련"}</span>
        <a href={article.url} target="_blank" rel="noreferrer">{article.title}</a>
        <small>{article.source_domain || article.source || "출처 미상"} · {formatDate(article.collected_at)}</small>
      </div>)}
    </div>
    {articles.length > EVIDENCE_VISIBLE_COUNT && <button type="button" className="brief-evidence-more" onClick={() => setExpanded((current) => !current)}>
      {expanded ? "간략히 보기" : `근거 기사 ${formatNumber(articles.length)}건 전체 보기`}
    </button>}
  </>;
}

function TrustStrip({ risk, content, scenario }) {
  const violations = scenario?.verification?.violations ?? [];
  const verified = scenario?.verification ? scenario.verification.passed && violations.length === 0 : null;
  return <article className="brief-panel">
    <div className="brief-panel-head"><div><h3>신뢰도 확인</h3><p className="sub">이 판단이 무엇에 근거했는지</p></div></div>
    <div className="brief-trust-grid">
      <div className="brief-trust-item"><span>관련 보도</span><strong>{formatNumber(risk.evidence_article_count ?? risk.evidence_articles?.length ?? 0)}건</strong></div>
      <div className="brief-trust-item"><span>보도 출처</span><strong>{formatNumber(risk.source_count ?? 0)}곳</strong></div>
      <div className="brief-trust-item"><span>인용 법령</span><strong>{formatNumber(content?.regulations?.length ?? 0)}건</strong></div>
      <div className="brief-trust-item"><span>유사 사례</span><strong>{formatNumber(content?.precedents?.length ?? 0)}건</strong></div>
      {verified !== null && <div className={`brief-trust-item ${verified ? "pass" : "flag"}`}><span>자동 검증</span><strong>{verified ? "통과" : `확인 필요 ${violations.length}건`}</strong></div>}
    </div>
    <p className="brief-trust-note">AI가 수집한 보도와 판례를 근거로 생성한 판단입니다. 법적 자문을 대체하지 않으며, 실행 전 담당 부서 확인이 필요합니다.</p>
  </article>;
}

function HoldNotice({ content }) {
  const copy = HOLD_STATUS[content.status] ?? HOLD_STATUS["근거부족_보류"];
  return <div className="brief-hold">
    <span className="brief-badge warning">{copy.kicker}</span>
    <strong>{copy.headline}</strong>
    <p>{content.review_reason || "위험 판단에 사용된 기사를 확인할 수 없습니다."}</p>
    <small>{copy.guide}</small>
  </div>;
}

// 판정된 위험과 그 대응 전략을 한 화면에서 보여준다. 분석 파이프라인의 위험판정·대응
// 두 단계로 나뉘어 있던 내용을 하나로 합쳐, "이 위험 → 이래서 대응은 이렇다"가
// 클릭 한 번 없이 이어지게 한다.
export default function RiskBriefPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const companyId = searchParams.get("companyId");
  const eventId = searchParams.get("eventId");

  const [risk, setRisk] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drafts, setDrafts] = useState([]);
  const [generationStatus, setGenerationStatus] = useState("idle");
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState("");
  const [active, setActive] = useState(0);

  const loadRisk = useCallback(async () => {
    if (!companyId || !eventId) { setLoading(false); return; }
    setLoading(true); setError(null);
    try {
      const found = await findRiskEvent(companyId, eventId);
      setRisk(found);
      setGenerationStatus(found?.response_generation_status ?? "idle");
      if (!found) setError("해당 위험 이벤트를 찾을 수 없습니다.");
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  }, [companyId, eventId]);

  const loadDrafts = useCallback(async () => {
    if (!eventId) return;
    try {
      const response = await api.get(`/risk-events/${eventId}/response-drafts`);
      setDrafts(response.data ?? []);
    } catch (requestError) { setError(getErrorMessage(requestError)); }
  }, [eventId]);

  useEffect(() => { loadRisk(); }, [loadRisk]);
  useEffect(() => { setDrafts([]); setActive(0); loadDrafts(); }, [loadDrafts]);
  useEffect(() => {
    if (!["pending", "generating"].includes(generationStatus)) return undefined;
    const timer = window.setInterval(() => { loadRisk(); loadDrafts(); }, 5000);
    return () => window.clearInterval(timer);
  }, [generationStatus, loadDrafts, loadRisk]);

  if (loading) return <section className="workspace signal-scope brief-detail"><p className="empty-state">위험 정보를 불러오는 중입니다.</p></section>;
  if (error && !risk) return <section className="workspace signal-scope brief-detail"><div className="notice error">{error}</div></section>;
  if (!risk) return <section className="workspace signal-scope brief-detail"><p className="empty-state">확인할 위험 이벤트가 없습니다.</p></section>;

  const band = severityBandOf(risk);
  const types = [...(risk.risk_types ?? [])].sort((left, right) => Number(right.is_primary) - Number(left.is_primary));
  const v3Draft = drafts.find((draft) => draft.schema_version === 3);
  const latest = v3Draft ?? drafts[0];
  const content = latest?.content;
  const isCompetitorImpact = latest?.schema_version === 3 && latest?.generation_kind === "competitor_impact";
  const isMainV3 = latest?.schema_version === 3 && !isCompetitorImpact;
  const isHold = content && HOLD_STATUS[content.status];
  const canGenerate = ["idle", "pending", "generating", "deferred", "failed"].includes(generationStatus);
  const scenarios = isMainV3 && Array.isArray(content.scenarios) ? content.scenarios : [];
  const current = scenarios[active] ?? scenarios[0];
  const report = current?.report ?? {};
  const reviewed = latest && latest.approval_state !== "draft";

  const generate = async () => {
    setBusy(true); setError(null);
    try {
      const force = ["pending", "generating"].includes(generationStatus) ? "?force=true" : "";
      const response = await api.post(`/risk-events/${risk.id}/response-generation${force}`);
      setGenerationStatus(response.data?.status ?? "pending");
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBusy(false); }
  };
  const review = async (decision) => {
    if (!latest) return;
    setBusy(true); setError(null);
    try { await api.post(`/response-drafts/${latest.id}/${decision}`, { notes }); await loadDrafts(); }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setBusy(false); }
  };

  return <section className="workspace signal-scope brief-detail">
    <button type="button" className="brief-detail-back" onClick={() => navigate(`/main${companyId ? `?companyId=${companyId}` : ""}`)}>← 브리핑으로 돌아가기</button>

    <div className="brief-detail-head">
      <div>
        <div className="brief-detail-badges">
          <span className={`brief-badge ${band}`}>{SEVERITY_LABEL[band]}</span>
          {types.slice(0, 3).map((item) => <span className="brief-badge type" key={item.risk_type}>{RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type}</span>)}
        </div>
        <h1>{riskEventTitle(risk)}</h1>
      </div>
      <div className="brief-detail-score"><strong>{formatRiskProbability(risk.risk_probability)}</strong><span>위험도</span></div>
    </div>

    {error && <div className="notice error">{error}</div>}

    <div className="brief-detail-grid">
      <div className="brief-detail-main">
        <article className="brief-panel">
          <div className="brief-panel-head"><div><h3>판정 근거</h3><p className="sub">이 사건이 왜 위험으로 분류됐는지</p></div></div>
          <EvidenceList risk={risk} />
        </article>

        {!content && <article className="brief-panel">
          <div className="brief-panel-head"><div><h3>대응 전략</h3></div></div>
          <p className="brief-empty-note">{RESPONSE_STATUS_LABELS[generationStatus] === "생성 중" ? "대응 전략을 생성하고 있습니다." : "아직 생성된 대응 전략이 없습니다."}</p>
        </article>}

        {isHold && <HoldNotice content={content} />}

        {isCompetitorImpact && <article className="brief-panel"><div className="brief-panel-head"><div><h3>나의 기업에 미칠 영향</h3></div></div><PeerRecommendationContent content={content} /></article>}

        {isMainV3 && !isHold && current && <>
          {scenarios.length > 1 && <article className="brief-panel">
            <div className="brief-panel-head"><div><h3>대응 방향 선택</h3></div></div>
            <div className="brief-stance-tabs" role="tablist">
              {scenarios.map((scenario, index) => <button type="button" role="tab" aria-selected={index === active} className={`brief-stance-tab${index === active ? " active" : ""}`} onClick={() => setActive(index)} key={`${scenario.stance ?? "scenario"}-${index}`}>
                <strong>{scenario.report?.scenario_headline || STANCE_LABELS[scenario.stance] || humanize(scenario.stance) || `${index + 1}번째 대응안`}</strong>
                {scenario.report?.scenario_contrast && <small>{scenario.report.scenario_contrast}</small>}
              </button>)}
            </div>
          </article>}

          {!!report.strategies?.length && <article className="brief-panel">
            <div className="brief-panel-head"><div><h3>권장 대응 전략</h3><p className="sub">{content.risk_type_label ? `${content.risk_type_label} · ` : ""}{TIER_LABELS[content.tier] ? `대응 등급 ${TIER_LABELS[content.tier]}` : ""}</p></div></div>
            <div className="brief-strategy-grid">
              {report.strategies.map((strategy, index) => {
                const bullets = strategy.detail && strategy.detail.includes("\n") ? strategy.detail.split(/\r?\n+/).filter(Boolean) : null;
                return <div className="brief-strategy-card" key={`${strategy.title ?? "strategy"}-${index}`}>
                  <div className="brief-strategy-meta">
                    <span className="brief-strategy-index">{index + 1}</span>
                    <span className="brief-strategy-type">{STRATEGY_LABELS[strategy.strategy_type] ?? humanize(strategy.strategy_type)}</span>
                    {strategy.target_stakeholder && <small>· 대상 {strategy.target_stakeholder}</small>}
                  </div>
                  <h4>{strategy.title}</h4>
                  {bullets ? <ul>{bullets.map((line, order) => <li key={order}>{line}</li>)}</ul> : <BulletText text={strategy.detail} />}
                </div>;
              })}
            </div>
          </article>}

          {(report.summary_points?.length > 0 || report.risk_assessment) && <article className="brief-panel">
            <div className="brief-panel-head"><div><h3>핵심 상황</h3></div></div>
            <div className="brief-situation-grid">
              {report.summary_points?.length > 0 && <div className="brief-situation-card">
                <h5>상황 요약</h5>
                <dl className="brief-situation-rows">{summaryRows(report.summary_points).map((row, index) => <div key={index}><dt>{row.label}</dt><dd>{row.text}</dd></div>)}</dl>
              </div>}
              {(report.risk_assessment?.primary_risks?.length > 0 || report.risk_assessment?.secondary_risks?.length > 0) && <div className="brief-situation-card">
                <h5>우선 확인할 위험</h5>
                <dl className="brief-situation-rows">
                  {summaryRows(report.risk_assessment.primary_risks ?? [], "주요 위험").map((row, index) => <div key={`p-${index}`}><dt>{row.label}</dt><dd>{row.text}</dd></div>)}
                  {summaryRows(report.risk_assessment.secondary_risks ?? [], "추가 위험").map((row, index) => <div key={`s-${index}`}><dt>{row.label}</dt><dd>{row.text}</dd></div>)}
                </dl>
              </div>}
            </div>
          </article>}

          {!!report.checklist?.length && <article className="brief-panel">
            <div className="brief-panel-head"><div><h3>실행 계획</h3></div></div>
            <div className="brief-timeline">
              {bandsOf(report.checklist).map((band2) => <div className="brief-timeline-band" key={band2.label}>
                <div className="brief-timeline-band-head"><i aria-hidden="true" />{band2.label}<span>{band2.items.length}개</span></div>
                {band2.items.map((item, index) => <div className="brief-timeline-task" key={index}>
                  <strong>{item.task}</strong>
                  <small>{item.owner ? `담당 · ${item.owner}` : "담당 부서 확인 필요"}</small>
                  <span>{deadlineLabel(item.deadline_hours)}</span>
                </div>)}
              </div>)}
            </div>
          </article>}

          {(report.judgment_basis || content.regulations?.length > 0 || content.precedents?.length > 0) && <article className="brief-panel">
            <div className="brief-panel-head"><div><h3>더 확인하기</h3></div></div>
            <div className="brief-fold-stack" style={{ display: "grid", gap: 8 }}>
              {report.judgment_basis && <details className="brief-fold"><summary>판단 근거 (수치)</summary><div className="brief-fold-body"><BulletText text={report.judgment_basis} /></div></details>}
              {content.regulations?.length > 0 && <details className="brief-fold"><summary>관련 법령 {content.regulations.length}건</summary><div className="brief-fold-body"><ul>{content.regulations.map((regulation, index) => <li key={index}>{[regulation.law_name, regulation.article].filter(Boolean).join(" ")}{regulation.requirement ? ` — ${regulation.requirement}` : ""}</li>)}</ul></div></details>}
              {content.precedents?.length > 0 && <details className="brief-fold"><summary>유사 사례 {content.precedents.length}건</summary><div className="brief-fold-body"><ul>{content.precedents.map((precedent, index) => <li key={index}>{precedent.title}{precedent.lesson ? ` — ${precedent.lesson}` : ""}</li>)}</ul></div></details>}
            </div>
          </article>}

          {report.limitations && <div className="brief-limitations"><strong>사용 전 확인</strong><p>{report.limitations}</p></div>}
        </>}

        {latest && latest.schema_version !== 3 && <article className="brief-panel"><div className="brief-panel-head"><div><h3>대응 전략</h3><p className="sub">이전 형식으로 생성된 초안입니다</p></div></div><MainResponseContent content={content ?? {}} risk={risk} /></article>}
      </div>

      <div className="brief-detail-side">
        <TrustStrip risk={risk} content={content} scenario={current} />
        <article className="brief-panel">
          <div className="brief-panel-head"><div><h3>대응 방안 상태</h3></div></div>
          <div className="brief-actions">
            <span className="status">{RESPONSE_STATUS_LABELS[generationStatus] ?? "미생성"}</span>
            {canGenerate && <button type="button" className="primary" onClick={generate} disabled={busy}>{busy ? "요청 중" : ["pending", "generating"].includes(generationStatus) ? "다시 시작" : ["idle", "deferred"].includes(generationStatus) ? "생성" : "다시 시도"}</button>}
          </div>
          {latest && content && !isHold && !reviewed && <div className="brief-actions">
            <input type="text" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="검토 메모 (선택)" />
            <button type="button" onClick={() => review("approve")} disabled={busy}>승인</button>
            <button type="button" onClick={() => review("reject")} disabled={busy}>반려</button>
          </div>}
          {reviewed && <p className="brief-empty-note">{latest.approval_state === "approved" ? "승인 완료" : "반려 완료"}</p>}
        </article>
      </div>
    </div>
  </section>;
}
