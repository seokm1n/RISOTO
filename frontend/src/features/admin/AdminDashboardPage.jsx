import { useCallback, useEffect, useState } from "react";

import { api, getErrorMessage } from "../../api";
import { Metric, PanelTitle } from "../../shared/components";
import {
  DATA_QUALITY_LABELS,
  INCIDENT_STATUS_LABELS,
  SOURCE_LABELS,
  formatDate,
  formatNumber,
} from "../../shared/presentation";

const MONITORING_LABELS = {
  backfilling: "수집 중",
  warming: "수집 중",
  active: "수집 중",
  paused: "정지",
  archived: "보관됨",
  error: "장애",
};

function AdminHead({ kicker, title, description }) {
  return <div className="workspace-head admin-workspace-head">
    <div><span className="eyebrow">{kicker}</span><h1>{title}</h1><p>{description}</p></div>
  </div>;
}

function MemberPasswordReset({ member, onClose, onComplete }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true); setError(null);
    try {
      await api.post(`/admin/members/${member.id}/password-reset`, {
        new_password: password,
        new_password_confirmation: confirmation,
      });
      onComplete("비밀번호를 재설정했습니다. 해당 회원의 기존 로그인 세션은 종료되었습니다.");
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  return <div className="admin-modal-layer">
    <button className="company-edit-backdrop" type="button" onClick={onClose} aria-label="비밀번호 재설정 닫기" />
    <form className="admin-modal" onSubmit={submit}>
      <div className="admin-modal-head"><div><span className="eyebrow">PASSWORD RESET</span><h2>회원 비밀번호 재설정</h2><p>{member.email}</p></div><button className="company-edit-close" type="button" onClick={onClose} aria-label="닫기">×</button></div>
      {error && <div className="notice error" role="alert">{error}</div>}
      <label className="field-label" htmlFor="admin-reset-password">새 비밀번호</label>
      <input id="admin-reset-password" type="password" minLength={8} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" required autoFocus />
      <label className="field-label" htmlFor="admin-reset-confirmation">새 비밀번호 확인</label>
      <input id="admin-reset-confirmation" type="password" minLength={8} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="new-password" required />
      <div className="admin-modal-actions"><button className="secondary-button" type="button" onClick={onClose}>취소</button><button className="submit-button" type="submit" disabled={busy}>{busy ? "저장 중..." : "비밀번호 재설정"}</button></div>
    </form>
  </div>;
}

function MembersAdminView() {
  const [data, setData] = useState({ items: [], total: 0, page: 1, page_size: 50 });
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState(null);
  const [resetTarget, setResetTarget] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.get("/admin/members?page=1&page_size=50");
      setData(response.data);
      setNotice(null);
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return <section className="workspace admin-workspace admin-members-workspace">
    <div className="admin-members-shell">
      <AdminHead kicker="MEMBER MANAGEMENT / 01" title="회원 관리" description="일반 회원의 가입 현황과 등록 기업을 확인하고, 필요한 경우 비밀번호를 재설정합니다." />
      {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
      <div className="metric-grid dashboard-metrics admin-metrics"><Metric label="일반 회원" value={data.total} /></div>
      <section className="panel admin-members-panel">
        <div className="admin-panel-heading">
          <PanelTitle kicker="GENERAL MEMBERS ONLY" title="일반 회원 목록" />
          <button className={`admin-refresh-button${loading ? " loading" : ""}`} type="button" onClick={load} disabled={loading} aria-label="회원 목록 새로고침" title={loading ? "회원 목록 불러오는 중" : "회원 목록 새로고침"}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0 1.2 5.2" /><path d="M20 4v7h-7" /></svg>
          </button>
        </div>
        {loading ? <p className="panel-empty">회원 정보를 불러오는 중입니다.</p> : data.items.length ? <div className="admin-member-table-wrap"><table className="admin-member-table"><thead><tr><th>아이디</th><th>메인 기업</th><th>비교 기업 등록 수</th><th>비교 기업 목록</th><th>가입일</th><th>관리</th></tr></thead><tbody>{data.items.map((member) => <tr key={member.id}><td><strong>{member.email}</strong><small>{member.is_active ? "활성 회원" : "비활성 회원"}</small></td><td>{member.main_company_name || <span className="table-muted">미등록</span>}</td><td>{formatNumber(member.competitor_count)}개</td><td>{member.competitor_names.length ? member.competitor_names.join(", ") : <span className="table-muted">없음</span>}</td><td>{formatDate(member.created_at)}</td><td><button className="table-action-button" type="button" onClick={() => setResetTarget(member)}>비밀번호 재설정</button></td></tr>)}</tbody></table></div> : <p className="panel-empty">가입한 일반 회원이 없습니다.</p>}
      </section>
      <p className="admin-footnote">관리자 계정은 이 목록에 노출하지 않습니다. 회원 비밀번호는 해시로 저장되므로 기존 비밀번호를 조회하지 않고 새 비밀번호로만 재설정합니다.</p>
    </div>
    {resetTarget && <MemberPasswordReset member={resetTarget} onClose={() => setResetTarget(null)} onComplete={(message) => { setResetTarget(null); setNotice({ type: "success", message }); }} />}
  </section>;
}

function AdminIncidentList({ incidents, companies }) {
  const companyNames = new Map((companies ?? []).map((company) => [company.id, company.name]));
  if (!incidents?.length) return <p className="panel-empty">최근 수집 장애가 없습니다.</p>;
  return <div className="incident-list">{incidents.map((incident) => <article className={`incident-row ${incident.status}`} key={incident.id}>
    <div><span className={`quality-pill ${incident.data_quality}`}>{DATA_QUALITY_LABELS[incident.data_quality] ?? incident.data_quality}</span><strong>{["open", "retrying"].includes(incident.status) ? "복구 중" : INCIDENT_STATUS_LABELS[incident.status] ?? incident.status}</strong></div>
    <p>{incident.error_summary}</p>
    <small>예정 구간 {formatDate(incident.scheduled_for)} · 감지 {formatDate(incident.detected_at)} · 수집기 {(incident.sources ?? []).map((source) => SOURCE_LABELS[source] ?? source).join(", ") || "-"}</small>
    <small>영향 기업 {(incident.affected_company_ids ?? []).map((id) => companyNames.get(id) ?? `#${id}`).join(", ") || "-"} · 재시도 {incident.retry_count}/3</small>
  </article>)}</div>;
}

function CollectionAdminView() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.get("/admin/collection/overview?days=14");
      setData(response.data); setNotice(null);
    } catch (requestError) {
      setNotice({ type: "error", message: getErrorMessage(requestError) });
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const maxDaily = Math.max(...(data?.daily ?? []).flatMap((item) => [item.story_count, item.risk_count]), 1);

  return <section className="workspace admin-workspace">
    <AdminHead kicker="COLLECTION MANAGEMENT / 02" title="수집 관리" description="전체 회원의 수집 현황과 수집원 상태를 한 곳에서 확인합니다." />
    {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
    {loading && !data ? <p className="empty-state">전체 수집 현황을 불러오는 중입니다.</p> : <>
      <div className="metric-grid dashboard-metrics admin-metrics"><Metric label="수집중" value={data?.active_companies ?? 0} /><Metric label="전체 등록 기업수" value={data?.total_companies ?? 0} /><Metric label="전체 수집량" value={data?.collected_count ?? 0} /><Metric label="전체 위험량" value={data?.risk_count ?? 0} tone={data?.risk_count ? "danger" : "success"} /></div>
      <div className="admin-collection-grid"><section className="panel"><PanelTitle kicker="COLLECTION VOLUME / 14 DAYS" title="전체 수집량·위험량" /><div className="admin-daily-chart" role="img" aria-label="최근 14일 전체 수집량과 위험량"><div className="admin-daily-bars">{(data?.daily ?? []).map((item) => <div className="admin-daily-bar" key={item.day} title={`${item.day} · 수집 ${item.story_count}건 · 위험 ${item.risk_count}건`}><i style={{ height: `${Math.max(item.story_count ? 8 : 3, item.story_count / maxDaily * 100)}%` }} /><b style={{ height: `${Math.max(item.risk_count ? 8 : 3, item.risk_count / maxDaily * 100)}%` }} /><span>{new Date(item.day).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}</span></div>)}</div><div className="admin-chart-legend"><span><i className="collection" />수집량</span><span><i className="risk" />위험량</span></div></div></section><section className="panel"><PanelTitle kicker="COLLECTION PROVIDERS" title="수집 API 관리" /><div className="admin-provider-list">{(data?.providers ?? []).map((provider) => <div key={provider.source}><span>{provider.source}</span><strong className={provider.status === "연결됨" ? "connected" : "disconnected"}>{provider.status}</strong></div>)}</div></section></div>
      <section className="panel admin-company-overview"><PanelTitle kicker="ALL REGISTERED COMPANIES" title="기업별 수집 현황" /><div className="admin-company-table-wrap"><table className="admin-company-table"><thead><tr><th>기업</th><th>소유 회원</th><th>구분</th><th>상태</th><th>수집량</th><th>위험량</th><th>마지막 수집</th></tr></thead><tbody>{(data?.companies ?? []).map((company) => <tr key={company.id}><td><strong>{company.name}</strong></td><td>{company.owner_email}</td><td>{company.company_role === "main" ? "나의 기업" : "비교 기업"}</td><td><span className={`admin-status-pill ${company.monitoring_status}`}>{MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</span></td><td>{formatNumber(company.article_count)}건</td><td>{formatNumber(company.risk_count)}건</td><td>{formatDate(company.last_collected_at)}</td></tr>)}</tbody></table></div></section>
      <section className="panel admin-incidents-panel"><PanelTitle kicker="RECENT COLLECTION INCIDENTS" title="최근 수집 장애" /><AdminIncidentList incidents={data?.incidents} companies={data?.companies} /></section>
    </>}
  </section>;
}

export default function AdminDashboardPage({ view = "members" }) {
  return view === "collection" ? <CollectionAdminView /> : <MembersAdminView />;
}
