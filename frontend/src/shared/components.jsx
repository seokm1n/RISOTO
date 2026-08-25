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
