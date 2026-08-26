import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { api, getErrorMessage } from "../../api";
import { PanelTitle } from "../../shared/components";
import {
  ADVERTISEMENT_LABEL_OPTIONS,
  RELEVANCE_LABEL_OPTIONS,
  SENTIMENT_LABEL_OPTIONS,
  formatDate,
} from "../../shared/presentation";

const EMPTY_FORM = { relevance_label: "relevant", advertisement_label: "no", sentiment_label: "neutral", notes: "" };
const BATCH_SIZE = 30;

// AI 판정값을 보여주지 않고 사람이 기사 하나씩 직접 라벨링하는 블라인드 검수 화면이다.
export default function ArticleReviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const auditMode = searchParams.get("mode") === "audit";
  const [companies, setCompanies] = useState([]);
  const [companyId, setCompanyId] = useState("");
  const [queue, setQueue] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [notice, setNotice] = useState(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [labeledCount, setLabeledCount] = useState(0);

  const current = queue[0] ?? null;

  const loadCompanies = useCallback(async () => {
    try {
      const response = await api.get("/companies");
      setCompanies(response.data);
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    }
  }, []);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const params = { limit: BATCH_SIZE };
      if (companyId) params.company_id = companyId;
      const endpoint = auditMode ? "/reviews/llm-audit-sample" : "/reviews/articles";
      const response = await api.get(endpoint, { params });
      setQueue(response.data);
      setNotice(null);
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    } finally {
      setLoading(false);
    }
  }, [companyId, auditMode]);

  useEffect(() => { loadCompanies(); }, [loadCompanies]);
  useEffect(() => { loadQueue(); }, [loadQueue]);
  useEffect(() => { setForm(EMPTY_FORM); }, [current?.raw_article_id]);

  const submit = async (event) => {
    event.preventDefault();
    if (!current) return;
    setSubmitting(true);
    setNotice(null);
    try {
      await api.post("/reviews/articles", {
        company_id: current.company_id,
        raw_article_id: current.raw_article_id,
        ...form,
      });
      setLabeledCount((value) => value + 1);
      setQueue((items) => {
        const rest = items.slice(1);
        if (rest.length === 0) loadQueue();
        return rest;
      });
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    } finally {
      setSubmitting(false);
    }
  };

  const skip = () => setQueue((items) => items.slice(1));

  const remaining = queue.length;

  return <section className="workspace review-workspace">
    <div className="workspace-head">
      <div>
        <span className="eyebrow">{auditMode ? "LLM LABEL AUDIT SAMPLE" : "BLIND ARTICLE REVIEW"}</span>
        <h1>{auditMode ? "LLM 라벨 표본 검수" : "기사 검수"}</h1>
        {auditMode
          ? <p>LLM이 이미 확정 라벨을 매긴 기사 중 이달 무작위 표본입니다. AI 판정값을 보지 않은 채 직접 라벨링하면, 이 값과 LLM 라벨을 비교해 매달 일치율을 계산합니다.</p>
          : <p>AI 판정값을 보지 않은 채 기사 원문만 보고 관련성·광고·감성을 직접 라벨링합니다. 여기서 쌓인 확정 라벨은 모델 재학습·평가의 정답 데이터로 쓰입니다.</p>}
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        {!auditMode && <select value={companyId} onChange={(event) => setCompanyId(event.target.value)}>
          <option value="">전체 기업</option>
          {companies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}
        </select>}
        <button
          type="button"
          className="secondary-button"
          onClick={() => setSearchParams(auditMode ? {} : { mode: "audit" })}
        >
          {auditMode ? "일반 검수로" : "LLM 표본 검수로"}
        </button>
      </div>
    </div>
    {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
    <div className="review-progress">
      <div><span>이번 세션 라벨링</span><strong>{labeledCount}건</strong></div>
      <div><span>남은 검수 후보(불러온 배치 기준)</span><strong>{remaining}건</strong></div>
    </div>
    <section className="panel">
      <PanelTitle kicker="REVIEW QUEUE" title="검수 대상 기사" />
      {loading
        ? <p className="panel-empty">후보를 불러오는 중입니다.</p>
        : !current
          ? <p className="panel-empty">{auditMode ? "이번 달 LLM 표본 검수를 모두 마쳤습니다. 다음 달에 새 표본이 생깁니다." : "라벨링할 후보가 남아있지 않습니다. 실시간 수집이 더 쌓이면 새 후보가 생깁니다."}</p>
          : <div className="review-grid">
            <article className="review-article">
              <span>{current.company_name} · {formatDate(current.published_at)}</span>
              <h3>{current.title}</h3>
              {current.summary && <p>{current.summary}</p>}
              <small><a href={current.url} target="_blank" rel="noreferrer">원문 보기</a></small>
            </article>
            <form className="label-form" onSubmit={submit}>
              <label>
                관련성
                <select value={form.relevance_label} onChange={(event) => setForm((value) => ({ ...value, relevance_label: event.target.value }))}>
                  {RELEVANCE_LABEL_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
                </select>
              </label>
              <label>
                광고 여부
                <select value={form.advertisement_label} onChange={(event) => setForm((value) => ({ ...value, advertisement_label: event.target.value }))}>
                  {ADVERTISEMENT_LABEL_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
                </select>
              </label>
              <label>
                감성
                <select value={form.sentiment_label} onChange={(event) => setForm((value) => ({ ...value, sentiment_label: event.target.value }))}>
                  {SENTIMENT_LABEL_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
                </select>
              </label>
              <label className="label-wide">
                메모(선택)
                <textarea
                  value={form.notes}
                  maxLength={4000}
                  onChange={(event) => setForm((value) => ({ ...value, notes: event.target.value }))}
                  placeholder="판단 근거를 남겨두면 나중에 검토할 때 도움이 됩니다."
                />
              </label>
              <div className="label-wide" style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                <button type="button" className="secondary-button" onClick={skip} disabled={submitting}>건너뛰기</button>
                <button type="submit" className="submit-button" disabled={submitting}>{submitting ? "저장 중..." : "라벨 저장하고 다음"}</button>
              </div>
            </form>
          </div>}
    </section>
  </section>;
}
