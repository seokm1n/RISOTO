import { useCallback, useEffect, useRef, useState } from "react";

import {
  DATA_QUALITY_LABELS,
  INCIDENT_STATUS_LABELS,
  SOURCE_LABELS,
  formatDate,
  formatNumber,
} from "./presentation";

export const formatIncidentError = (value) => Object.entries(SOURCE_LABELS).reduce(
  (message, [source, label]) => message.replace(new RegExp(`^${source}:`, "i"), `${label}:`), String(value ?? ""),
);

export function AppNoticeDialog({ kicker, title, children, confirmLabel = "확인", cancelLabel = null, onConfirm, onClose, busy = false, tone = "default" }) {
  const dismiss = onClose ?? onConfirm;
  const cancelRef = useRef(null);
  const confirmRef = useRef(null);

  useEffect(() => {
    const previousFocus = document.activeElement;
    const focusTimer = window.setTimeout(() => (cancelRef.current ?? confirmRef.current)?.focus(), 0);
    return () => {
      window.clearTimeout(focusTimer);
      previousFocus?.focus?.();
    };
  }, []);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const ownsScrollLock = previousOverflow !== "hidden";
    if (ownsScrollLock) document.body.style.overflow = "hidden";
    const closeWithEscape = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!busy) dismiss?.();
    };
    window.addEventListener("keydown", closeWithEscape, true);
    return () => {
      if (ownsScrollLock) document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeWithEscape, true);
    };
  }, [busy, dismiss]);

  return <div className="app-notice-layer">
    <button className="company-edit-backdrop" type="button" onClick={dismiss} aria-label={`${title} 안내창 닫기`} disabled={busy} tabIndex={-1} />
    <section className={`app-notice-dialog ${tone}`} role="dialog" aria-modal="true" aria-label={title}>
      <div className="app-notice-mark" aria-hidden="true"><span>{tone === "danger" ? "!" : "i"}</span></div>
      <span className="eyebrow">{kicker}</span>
      <h2>{title}</h2>
      <div className="app-notice-copy">{children}</div>
      <div className="app-notice-actions">
        {cancelLabel && <button ref={cancelRef} className="secondary-button" type="button" onClick={onClose} disabled={busy}>{cancelLabel}</button>}
        <button ref={confirmRef} className="submit-button app-notice-primary" type="button" onClick={onConfirm} disabled={busy} aria-busy={busy}>{busy ? "처리 중..." : confirmLabel}</button>
      </div>
    </section>
  </div>;
}

// 브라우저 기본 confirm 대신 앱 디자인을 유지하면서 기존의 true/false 흐름을 제공한다.
export function useAppConfirm() {
  const [options, setOptions] = useState(null);
  const resolverRef = useRef(null);

  const confirm = useCallback((nextOptions) => new Promise((resolve) => {
    resolverRef.current?.(false);
    resolverRef.current = resolve;
    setOptions(typeof nextOptions === "string" ? { title: nextOptions } : nextOptions);
  }), []);

  const settle = useCallback((confirmed) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    setOptions(null);
    resolve?.(confirmed);
  }, []);

  useEffect(() => () => {
    resolverRef.current?.(false);
    resolverRef.current = null;
  }, []);

  const confirmationDialog = options ? <AppNoticeDialog
    kicker={options.kicker ?? "PLEASE CONFIRM"}
    title={options.title}
    confirmLabel={options.confirmLabel ?? "확인"}
    cancelLabel={options.cancelLabel ?? "취소"}
    onConfirm={() => settle(true)}
    onClose={() => settle(false)}
    tone={options.tone ?? "default"}
  >
    {options.message && <p>{options.message}</p>}
    {options.detail && <p className="app-notice-detail">{options.detail}</p>}
  </AppNoticeDialog> : null;

  return { confirm, confirmationDialog, confirming: Boolean(options) };
}

// 수집 장애는 기업 위험과 분리해 예정 구간·영향 범위·재시도 상태를 표시한다.
export function IncidentList({ incidents = [], companies = [], onAcknowledge }) {
  const companyNames = new Map(companies.map((company) => [company.id, company.name]));
  if (!incidents.length) return <p className="panel-empty">최근 수집 장애가 없습니다.</p>;
  return <div className="incident-list">{incidents.map((incident) => <article className={`incident-row ${incident.status}`} key={incident.id}>
    <div><span className={`quality-pill ${incident.data_quality}`}>{DATA_QUALITY_LABELS[incident.data_quality]}</span><strong>{INCIDENT_STATUS_LABELS[incident.status] ?? incident.status}</strong></div>
    <p>{formatIncidentError(incident.error_summary)}</p>
    <small>예정 구간 {formatDate(incident.scheduled_for)} · 감지 {formatDate(incident.detected_at)} · 수집기 {incident.sources.map((source) => SOURCE_LABELS[source] ?? source).join(", ") || "-"}</small>
    <small>영향 기업 {incident.affected_company_ids.map((id) => companyNames.get(id) ?? `#${id}`).join(", ") || "-"} · 재시도 {incident.retry_count}/3{incident.next_retry_at ? ` · 다음 ${formatDate(incident.next_retry_at)}` : ""}</small>
    {onAcknowledge && !incident.acknowledged_at && !["acknowledged", "recovered"].includes(incident.status) && <button type="button" onClick={() => onAcknowledge(incident.id)}>장애 확인</button>}
  </article>)}</div>;
}

export function Metric({ label, value, tone = "", small = false }) {
  return <article className={`metric ${tone}`}><span>{label}</span><strong className={small ? "metric-date" : ""}>{small ? value : formatNumber(value)}</strong></article>;
}

// 전체 항목 수를 기준으로 이전·다음 페이지 이동 UI를 표시한다.
export function Pagination({ page, pageSize, total, onChange }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return total ? <p className="page-count">총 {formatNumber(total)}건</p> : null;
  return <div className="pagination"><span>{page} / {pages} · 총 {formatNumber(total)}건</span><button type="button" onClick={() => onChange(page - 1)} disabled={page <= 1}>이전</button><button type="button" onClick={() => onChange(page + 1)} disabled={page >= pages}>다음</button></div>;
}

// 패널의 보조 문구와 제목을 공통 레이아웃으로 표시한다.
export function PanelTitle({ kicker, title }) {
  return <div className="panel-title"><span className="eyebrow">{kicker}</span><h2>{title}</h2></div>;
}
