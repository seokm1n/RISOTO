import { useCallback, useEffect, useState } from "react";

import { api, getErrorMessage } from "../../api";
import {
  DATA_QUALITY_LABELS,
  FILTER_REASON_LABELS,
  LIGHTGBM_STATE_LABELS,
  RISK_TYPE_LABELS,
  SOURCE_LABELS,
  formatDate,
  formatNumber,
  formatPercent,
  formatRiskProbability,
  formatScore,
} from "../../shared/presentation";

// RealtimePage가 위험 이벤트·대응 초안·7일 추세를 그리는 데 쓰는 패널 조각들이다.
// 화면 하나가 데이터 페칭·상태 관리까지 전부 떠안지 않도록 표시 전용 컴포넌트만 분리했다.

// 거부 또는 검토 대기 중인 원문 기사와 필터 판정 근거를 표시한다.
export function FilterResultRow({ result }) {
  const reasonText = FILTER_REASON_LABELS[result.reason] ?? result.reason;
  const decisionText = result.decision === "review_required" ? `${reasonText} 검토` : `${reasonText} 제외`;
  const methodText = result.classifier_kind === "rules_only" ? "규칙 판정" : "자동 판정";
  return <a className="article-row filter-result-row" href={result.url} target="_blank" rel="noreferrer">
    <span className={`filter-pill ${result.decision}`}>{decisionText}</span>
    <div><strong>{result.title}</strong>
      <small>{SOURCE_LABELS[result.source] ?? result.source} · 판정 {formatDate(result.filtered_at)}</small>
      <small className="filter-scores">관련성 {formatScore(result.relevance_score)} · 광고성 {formatScore(result.advertising_score)} · 신뢰도 {formatScore(result.confidence)} · {methodText}</small>
    </div>
  </a>;
}

// 최신 15분 특징 창과 수집 완전성, 공통 모델 상태를 요약한다.
export function FeatureWindowSummary({ window: featureWindow }) {
  if (!featureWindow) return <p className="panel-empty">아직 생성된 15분 특징 구간이 없습니다.</p>;
  return <div className="feature-window-summary">
    <div className="feature-window-head"><div><span className="eyebrow">LATEST 15-MIN WINDOW</span><strong>{formatDate(featureWindow.window_start)}–{formatDate(featureWindow.window_end)}</strong></div><div><span className={`quality-pill ${featureWindow.data_quality}`}>{DATA_QUALITY_LABELS[featureWindow.data_quality]}</span><span className={`model-pill ${featureWindow.model_state}`}>{LIGHTGBM_STATE_LABELS[featureWindow.model_state] ?? "LightGBM 상태 확인 필요"}</span></div></div>
    <div className="window-metrics"><div><span>기사</span><strong>{formatNumber(featureWindow.article_count)}</strong></div><div><span>스토리</span><strong>{formatNumber(featureWindow.story_count)}</strong></div><div><span>확산</span><strong>{formatNumber(featureWindow.amplification_count)}</strong></div><div><span>언론사</span><strong>{formatNumber(featureWindow.publisher_count)}</strong></div><div title="이상 탐지 모델이 평소 패턴과 얼마나 다른지 계산한 원점수입니다."><span>이상 점수</span><strong>{formatScore(featureWindow.anomaly_score)}</strong></div><div title="최근 구간들 중 이상 점수가 몇 번째로 높은 백분위인지 나타냅니다."><span>이상치 백분위</span><strong>{formatPercent(featureWindow.anomaly_percentile)}</strong></div><div title="운영 중인 LightGBM이 현재 구간의 최종 위험 가능성을 산출합니다."><span>위험도</span><strong>{formatRiskProbability(featureWindow.risk_probability)}</strong></div></div>
    {featureWindow.data_quality === "unavailable" && <p className="window-warning">수집 불가 구간이므로 위험도를 계산하지 않았습니다.</p>}
  </div>;
}

const HORIZON_LABELS = { immediate: "즉시", within_24h: "24시간 이내", within_7d: "7일 이내" };

function ActionGroups({ actions }) {
  return Object.entries(actions ?? {}).map(([horizon, items]) => <section className="scenario-actions" key={horizon}>
    <h5>{HORIZON_LABELS[horizon] ?? horizon}</h5>
    {(items ?? []).map((item, index) => <div className="scenario-action" key={`${horizon}-${index}`}><p>{typeof item === "string" ? item : item.action}</p>{typeof item !== "string" && item.evidence_urls?.map((url, urlIndex) => <a href={url} target="_blank" rel="noreferrer" key={url}>근거 {urlIndex + 1}</a>)}</div>)}
  </section>);
}

function ResponseDraftContent({ draft }) {
  const content = draft.content ?? {};
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const isCompetitorImpact = draft.generation_kind === "competitor_impact";
  return <div className="response-draft">
    <div className="response-draft-head"><div><span className="eyebrow">RESPONSE DRAFT · REVIEW REQUIRED</span><strong>{content.risk_summary}</strong></div><span className={`draft-kind ${isCompetitorImpact ? "competitor" : "main"}`}>{isCompetitorImpact ? "경쟁사 → 메인 기업 영향" : "메인 기업 직접 대응"}</span></div>
    {scenarios.length ? <div className="response-scenario-list">{scenarios.map((scenario, index) => <article className="response-scenario" key={`${scenario.title ?? "scenario"}-${index}`}>
      <header><span>경우 {String(index + 1).padStart(2, "0")}</span><h4>{scenario.title || `${index + 1}번째 대응안`}</h4></header>
      {scenario.assumption && <p><strong>전제</strong>{scenario.assumption}</p>}
      {scenario.possible_impact && <p><strong>메인 기업 예상 영향</strong>{scenario.possible_impact}</p>}
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
  useEffect(() => { loadDrafts(); }, [loadDrafts]);
  if (!risk) return <p className="panel-empty">확인할 위험 이벤트를 선택해 주세요.</p>;
  const latest = drafts[0]; const content = latest?.content;
  const generate = async () => { setLoading(true); setError(null); try { await api.post(`/risk-events/${risk.id}/response-drafts`); await loadDrafts(); } catch (requestError) { setError(getErrorMessage(requestError)); } finally { setLoading(false); } };
  const review = async (decision) => {
    if (!latest) return;
    setLoading(true); setError(null);
    try { await api.post(`/response-drafts/${latest.id}/${decision}`, { notes }); await loadDrafts(); }
    catch (requestError) { setError(getErrorMessage(requestError)); } finally { setLoading(false); }
  };
  return <div className="risk-detail">
    <div className="risk-detail-head"><div><span className={`severity ${risk.severity}`}>{risk.severity === "critical" ? "긴급" : "주의"}</span><h3>{risk.summary || risk.article_title || `위험 이벤트 #${risk.id}`}</h3></div><span className={`model-pill ${risk.model_state}`}>{LIGHTGBM_STATE_LABELS[risk.model_state] ?? "LightGBM 상태 확인 필요"}</span></div>
    <p>위험도 {formatRiskProbability(risk.risk_probability)} · 이상 점수 {formatScore(risk.anomaly_score)} · {formatDate(risk.detected_at)}</p>
    <div className="risk-type-list">{risk.risk_types.map((item) => <span key={item.risk_type}>{RISK_TYPE_LABELS[item.risk_type] ?? item.risk_type} {formatPercent(item.probability)}</span>)}</div>
    <div className="evidence-list"><strong>근거 기사{risk.evidence_articles.length > 5 ? ` (관련도 상위 5건 / 전체 ${risk.evidence_articles.length}건)` : ""}</strong>{risk.evidence_articles.length ? risk.evidence_articles.slice(0, 5).map((article) => <a key={article.article_id} href={article.url} target="_blank" rel="noreferrer">{article.title}</a>) : <small>연결된 근거 기사가 없습니다.</small>}</div>
    {!latest && <button className="secondary-button" type="button" onClick={generate} disabled={loading || !risk.evidence_articles.length}>{loading ? "생성 중..." : "근거 기반 대응 초안 생성"}</button>}
    {error && <div className="notice error">{error}</div>}
    {content && <><ResponseDraftContent draft={latest} />{canReview ? <div className="draft-review"><input value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="검토 메모 (선택)" /><button type="button" onClick={() => review("approve")} disabled={loading || latest.approval_state !== "draft"}>승인</button><button type="button" onClick={() => review("reject")} disabled={loading || latest.approval_state !== "draft"}>반려</button><span>{latest.approval_state === "draft" ? "외부 전송·실행 금지" : latest.approval_state === "approved" ? `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}승인 완료` : `${latest.reviewed_by ? `${latest.reviewed_by} · ` : ""}반려됨`}</span></div> : <div className="draft-review readonly"><span>{latest.approval_state === "draft" ? "멤버 승인 대기" : latest.approval_state === "approved" ? "승인 완료" : "반려됨"}</span></div>}</>}
  </div>;
}

// 일별 기사 수와, 운영 LightGBM이 준비된 경우에만 위험 수를 겹친 선 그래프로 표시한다.
export function OverlayLineChart({ overview, riskAvailable = true }) {
  const items = overview?.daily ?? [];
  if (!items.length) return <p className="panel-empty">최근 7일간 표시할 수집 데이터가 없습니다.</p>;

  // 고정 viewBox 안에서 기사 수와 위험 수를 각각 독립적인 최대값으로 정규화한다.
  const width = 900; const height = 280;
  const left = 52; const right = 52; const top = 24; const bottom = 42;
  const plotWidth = width - left - right; const plotHeight = height - top - bottom;
  const collectionMax = Math.max(...items.map((item) => item.article_count), 1);
  const riskMax = Math.max(...items.map((item) => item.risk_count), 1);
  // 일별 데이터 인덱스를 SVG 가로 좌표로 변환한다.
  const x = (index) => left + (items.length === 1 ? plotWidth / 2 : index / (items.length - 1) * plotWidth);
  // 기사 수를 왼쪽 축 기준 SVG 세로 좌표로 변환한다.
  const collectionY = (value) => top + plotHeight - value / collectionMax * plotHeight;
  // 위험 수를 오른쪽 축 기준 SVG 세로 좌표로 변환한다.
  const riskY = (value) => top + plotHeight - value / riskMax * plotHeight;
  const collectionPoints = items.map((item, index) => `${x(index)},${collectionY(item.article_count)}`).join(" ");
  const riskPoints = items.map((item, index) => `${x(index)},${riskY(item.risk_count)}`).join(" ");

  return <div className="overlay-chart">
    <div className="trend-legend"><span className="collection-line">수집량 <strong>{formatNumber(overview.article_count)}건</strong></span><span className="risk-line">위험량 <strong>{riskAvailable ? `${formatNumber(overview.risk_count)}건` : "판정 대기"}</strong></span><small>{riskAvailable ? "수집량 좌측 축 · 위험량 우측 축" : "운영 LightGBM 준비 후 위험 추세 제공"}</small></div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={riskAvailable ? "최근 7일 전체 수집량과 위험량 선 그래프" : "최근 7일 전체 수집량 선 그래프, 위험량은 판정 대기"}>
      {[0, .25, .5, .75, 1].map((ratio) => { const y = top + ratio * plotHeight; return <g key={ratio}><line className="trend-grid-line" x1={left} x2={width - right} y1={y} y2={y} /><text className="trend-axis-label" x={left - 10} y={y + 4} textAnchor="end">{Math.round(collectionMax * (1 - ratio))}</text>{riskAvailable && <text className="trend-axis-label risk-axis-label" x={width - right + 10} y={y + 4}>{Math.round(riskMax * (1 - ratio))}</text>}</g>; })}
      <polyline className="trend-line collection" points={collectionPoints} />
      {riskAvailable && <polyline className="trend-line risk" points={riskPoints} />}
      {items.map((item, index) => <g key={item.day}><circle className="trend-dot collection" cx={x(index)} cy={collectionY(item.article_count)} r="4" />{riskAvailable && <circle className="trend-dot risk" cx={x(index)} cy={riskY(item.risk_count)} r="4" />}<text className="trend-date-label" x={x(index)} y={height - 13} textAnchor="middle">{new Date(item.day).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}</text></g>)}
    </svg>
  </div>;
}
