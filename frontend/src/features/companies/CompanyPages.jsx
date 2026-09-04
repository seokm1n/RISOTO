import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { api, getErrorMessage } from "../../api";
import { AppNoticeDialog, useAppConfirm } from "../../shared/components";
import {
  COMPANY_KEYWORD_FIELDS,
  COMPANY_SIZE_LABELS,
  KEYWORD_LABELS,
  MONITORING_LABELS,
} from "../../shared/presentation";
import {
  companyFormSignature,
  companyKeywordPayload,
  companyKeywordsWithDrafts,
  companyToForm,
  emptyKeywordDrafts,
  isValidAnnualRevenue,
} from "./companyForm";

const COMPETITOR_LABEL = "비교 기업";

function KeywordInput({ id, label, usage, hint, values, onChange, onDraftChange, disabled = false }) {
  const [draft, setDraft] = useState("");
  const changeDraft = (value) => { setDraft(value); onDraftChange?.(value); };
  const addValue = () => {
    const normalized = draft.trim().replace(/\s+/g, " ");
    if (!normalized) return;
    if (!values.some((value) => value.toLocaleLowerCase() === normalized.toLocaleLowerCase())) onChange([...values, normalized]);
    changeDraft("");
  };

  return <div>
    <label htmlFor={id} className="field-label field-label-with-note"><span>{label}</span>{usage && <small>{usage}</small>}</label>
    <div className="keyword-shell focus-within:ring-2 focus-within:ring-[#756e69]/25">
      {values.map((value) => <span className="keyword-chip" key={value}>{value}<button type="button" aria-label={`${value} 삭제`} disabled={disabled} onClick={() => onChange(values.filter((item) => item !== value))}>×</button></span>)}
      <input id={id} value={draft} onChange={(event) => changeDraft(event.target.value)} onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === ",") { event.preventDefault(); addValue(); }
        if (event.key === "Backspace" && !draft && values.length) onChange(values.slice(0, -1));
      }} onBlur={addValue} placeholder={values.length ? "추가 입력" : hint} autoComplete="off" maxLength={200} disabled={disabled} />
    </div>
    <p className="field-hint">Enter 또는 쉼표로 여러 항목을 추가할 수 있습니다.</p>
  </div>;
}

function CompanySettingsFields({ idPrefix, form, industries, disabled, version, onFieldChange, onKeywordChange, onKeywordDraftChange }) {
  return <div className="edit-form-grid">
    <div><label className="field-label" htmlFor={`${idPrefix}-company-name`}>기업명</label><input className="text-field" id={`${idPrefix}-company-name`} value={form.name} disabled={disabled} onChange={(event) => onFieldChange("name", event.target.value)} placeholder="기업명" maxLength={200} required /></div>
    <div><label className="field-label field-label-with-note" htmlFor={`${idPrefix}-ticker`}><span>종목코드</span><small>상장기업만 입력</small></label><input className="text-field" id={`${idPrefix}-ticker`} value={form.ticker} disabled={disabled} onChange={(event) => onFieldChange("ticker", event.target.value)} placeholder="선택 입력" maxLength={30} /></div>
    <div><label className="field-label" htmlFor={`${idPrefix}-industry`}>산업군</label><select className="text-field" id={`${idPrefix}-industry`} value={form.industryId} disabled={disabled} onChange={(event) => onFieldChange("industryId", event.target.value)} required><option value="">산업군을 선택</option>{industries.map((industry) => <option value={industry.id} key={industry.id}>{industry.name}</option>)}</select></div>
    <div><label className="field-label" htmlFor={`${idPrefix}-size-class`}>기업 규모</label><select className="text-field" id={`${idPrefix}-size-class`} value={form.sizeClass} disabled={disabled} onChange={(event) => onFieldChange("sizeClass", event.target.value)} required><option value="">기업 규모를 선택</option>{Object.entries(COMPANY_SIZE_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></div>
    <div className="edit-field-wide"><label className="field-label field-label-with-note" htmlFor={`${idPrefix}-annual-revenue`}><span>연매출</span><small>억원, 소수 둘째 자리까지</small></label><div className="revenue-field"><input className="text-field" id={`${idPrefix}-annual-revenue`} value={form.annualRevenue} disabled={disabled} onChange={(event) => onFieldChange("annualRevenue", event.target.value.replace(/[^\d.]/g, ""))} placeholder="예: 1000.00" inputMode="decimal" required /><span>억원</span></div></div>
    {COMPANY_KEYWORD_FIELDS.map(({ field, label, usage, hint }) => <KeywordInput key={`${idPrefix}-${version}-${field}`} id={`${idPrefix}-${field}-keywords`} label={label} usage={usage} hint={hint} values={form[field]} onChange={(values) => onKeywordChange(field, values)} onDraftChange={(value) => onKeywordDraftChange(field, value)} disabled={disabled} />)}
  </div>;
}

const formatRevenue = (value) => {
  if (value === null || value === undefined || value === "") return "미입력";
  const number = Number(value);
  return Number.isFinite(number) ? `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(number)}억원` : `${value}억원`;
};

export function CompanyCard({ company, onOpen, onEdit }) {
  const grouped = useMemo(() => {
    const result = { alias: [], product: [], risk: [] };
    company.keywords?.forEach((keyword) => result[keyword.keyword_type]?.push(keyword.value));
    return result;
  }, [company]);
  const roleLabel = company.company_role === "main" ? "나의 기업" : COMPETITOR_LABEL;
  const collectionRunning = ["backfilling", "warming", "active"].includes(company.monitoring_status);
  const monitoringLabel = MONITORING_LABELS[company.monitoring_status] ?? "상태 확인 필요";

  return <article className={`company-card ${company.company_role === "main" ? "main-company-card" : ""} ${onEdit ? "editable-company-card" : ""}`}>
    <div className="company-card-head flex items-start justify-between gap-5">
      <div><div className="company-role-line"><span className={`company-role-badge ${company.company_role}`}>{roleLabel}</span></div><div className="company-card-status-line"><span className={`status-dot ${collectionRunning ? "running" : "stopped"}`} aria-hidden="true" /><div><h3><button className="company-name-link" type="button" onClick={() => onOpen(company.id)}>{company.name}</button></h3><p>{company.industry_name} · {monitoringLabel}</p></div></div></div>
      {onEdit && <div className="company-card-controls"><button className="company-edit-icon" type="button" onClick={() => onEdit(company.id)} aria-label={`${company.name} 수정`} title="기업 수정"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4l11-11-4-4L4 16v4Z" /><path d="m13.5 6.5 4 4" /></svg><span>수정</span></button></div>}
    </div>
    <div className="company-finance-summary"><div><span>연매출</span><strong>{formatRevenue(company.annual_revenue_100m_krw)}</strong></div><div><span>기업 규모</span><strong>{COMPANY_SIZE_LABELS[company.company_size_class] ?? "미입력"}</strong></div></div>
    <div className="company-card-keywords mt-6 grid gap-4 sm:grid-cols-3">{Object.entries(KEYWORD_LABELS).map(([type, label]) => <div key={type}><span className="mini-label">{label}</span><p className="mt-1 text-sm leading-6 text-[#4e4642]">{grouped[type].join(" · ") || "등록 없음"}</p></div>)}</div>
  </article>;
}

function validateCompanyForm(form, keywordCount) {
  if (!form.name.trim() || !form.industryId || !form.sizeClass || !form.annualRevenue.trim()) return "기업명, 산업군, 기업 규모와 연매출을 모두 입력해 주세요.";
  if (!isValidAnnualRevenue(form.annualRevenue)) return "연매출은 0보다 큰 숫자로 소수 둘째 자리까지 입력해 주세요.";
  if (keywordCount > 100) return "키워드는 전체 100개까지 저장할 수 있습니다.";
  return null;
}

function companyPayload(form, submittedKeywords) {
  return {
    name: form.name.trim(),
    ticker: form.ticker.trim().toUpperCase() || null,
    industry_id: Number(form.industryId),
    annual_revenue_100m_krw: form.annualRevenue.trim(),
    company_size_class: form.sizeClass,
    keywords: companyKeywordPayload(submittedKeywords),
  };
}

function SetupPage({ companyRole = "competitor", onCreated, onOpenCompany, onEditCompany, onRegister, onboarding = false, registrationOnly = false, showRegistrationForm = true, refreshKey = 0 }) {
  const [industries, setIndustries] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [form, setForm] = useState(() => companyToForm(null));
  const [keywordDrafts, setKeywordDrafts] = useState(() => emptyKeywordDrafts());
  const [formVersion, setFormVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState(null);
  const { confirm, confirmationDialog } = useAppConfirm();
  const submittedKeywords = companyKeywordsWithDrafts(form, keywordDrafts);
  const keywordCount = Object.values(submittedKeywords).reduce((sum, values) => sum + values.length, 0);
  const targetLabel = companyRole === "main" ? "나의 기업" : COMPETITOR_LABEL;

  const changeField = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const changeKeyword = (field, values) => setForm((current) => ({ ...current, [field]: values }));
  const changeKeywordDraft = (field, value) => setKeywordDrafts((current) => ({ ...current, [field]: value }));

  const loadData = useCallback(async () => {
    try {
      const industryResponse = await api.get("/industries");
      setIndustries(industryResponse.data);
      if (!onboarding && !registrationOnly) {
        const companyResponse = await api.get("/companies");
        const registeredCompanies = companyResponse.data;
        setCompanies(registeredCompanies);
      }
      setNotice(null);
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setLoading(false); }
  }, [onboarding, registrationOnly]);

  useEffect(() => {
    loadData();
    if (onboarding || registrationOnly) return undefined;
    const timer = window.setInterval(loadData, 30000);
    return () => window.clearInterval(timer);
  }, [loadData, onboarding, refreshKey]);

  const submit = async (event) => {
    event.preventDefault();
    const validationError = validateCompanyForm(form, keywordCount);
    if (validationError) { setNotice({ type: "error", message: validationError }); return; }
    const confirmed = await confirm({
      kicker: companyRole === "competitor" ? "COMPETITOR REGISTRATION" : "MAIN COMPANY REGISTRATION",
      title: companyRole === "competitor"
        ? `${form.name.trim()}을(를) 비교 기업으로 등록할까요?`
        : `${form.name.trim()}을(를) 나의 기업으로 등록할까요?`,
      message: "등록 후 실시간 수집을 시작합니다.",
      confirmLabel: "등록",
    });
    if (!confirmed) return;
    setSubmitting(true); setNotice(null);
    try {
      const endpoint = companyRole === "main" ? "/companies/main" : "/companies";
      const response = await api.post(endpoint, { ...companyPayload(form, submittedKeywords), backfill_days: 7 });
      if (onCreated) await onCreated(response.data);
      if (!onboarding && !registrationOnly) {
        setForm(companyToForm(null)); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); await loadData();
        setNotice({ type: "success", message: `${response.data.name}을(를) ${companyRole === "main" ? "나의 기업으로" : "비교 기업으로"} 등록하고 수집을 시작했습니다.` });
      }
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setSubmitting(false); }
  };

  const registrationForm = <form className={registrationOnly ? "edit-card company-registration-form" : "setup-card"} onSubmit={submit}>
    {onboarding && <div className="card-heading onboarding-card-heading"><div><span className="eyebrow">MY COMPANY SETUP</span><h2>나의 기업 등록</h2><h3><strong>나의 기업은 위험 대응의 기준이 됩니다.<br />등록 후 정보는 수정할 수 있지만 삭제하거나 역할을 바꿀 수 없습니다.</strong></h3></div></div>}
    <CompanySettingsFields idPrefix={companyRole === "main" ? "main-register" : "competitor-register"} form={form} industries={industries} disabled={submitting || loading} version={formVersion} onFieldChange={changeField} onKeywordChange={changeKeyword} onKeywordDraftChange={changeKeywordDraft} />
    {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
    <button className="submit-button" type="submit" disabled={submitting || loading}><span>{submitting ? "등록 중..." : `${targetLabel} 등록`}</span><b aria-hidden="true">→</b></button>
  </form>;

  if (registrationOnly) return <>{registrationForm}{confirmationDialog}</>;

  return <>
    {showRegistrationForm && <section className={`hero-grid ${onboarding ? "onboarding-hero" : "competitor-hero"}`}>
      {!onboarding && <div className="competitor-setup-heading"><span className="eyebrow">NEW COMPETITOR TARGET</span><h1>{COMPETITOR_LABEL} 등록</h1></div>}
      {registrationForm}
    </section>}
    {!onboarding && <section className="registered-section">
      <p className="company-page-intro">등록된 기업 정보를 수정할 수 있고, 비교 기업을 새로 등록하거나 삭제할 수 있습니다.</p>
      {loading ? <p className="empty-state">기업 정보를 불러오는 중입니다.</p> : <div className="company-role-sections">
        {[
          { role: "main", title: "나의 기업", kicker: "MY COMPANY", empty: "등록한 나의 기업이 없습니다." },
          { role: "competitor", title: COMPETITOR_LABEL + " 목록", kicker: "REGISTERED COMPETITORS", empty: "아직 등록한 비교 기업이 없습니다." },
        ].map((group) => {
          const roleCompanies = companies.filter((company) => company.company_role === group.role);
          return <section className={`company-role-section ${group.role}`} key={group.role}>
            <div className="section-title company-role-section-title"><div><span className="eyebrow">{group.kicker}</span><h3>{group.title}</h3></div>{group.role === "competitor" && onRegister && <button className="company-register-button" type="button" onClick={onRegister}><span>{COMPETITOR_LABEL} 등록</span><b aria-hidden="true">＋</b></button>}</div>
            {roleCompanies.length ? <div className="company-list">{roleCompanies.map((company) => <CompanyCard company={company} key={company.id} onOpen={onOpenCompany} onEdit={onEditCompany} />)}</div> : <p className="empty-state">{group.empty}</p>}
          </section>;
        })}
      </div>}
    </section>}
    {confirmationDialog}
  </>;
}

export function MainCompanyOnboardingPage({ onCreated }) {
  const navigate = useNavigate();
  const [createdCompany, setCreatedCompany] = useState(null);
  const [completing, setCompleting] = useState(false);
  useEffect(() => { document.title = "RISOTO · 나의 기업 등록"; }, []);
  const complete = (company) => setCreatedCompany(company);
  const acknowledgeCollectionStart = async () => {
    setCompleting(true);
    try {
      await onCreated();
      navigate("/main", { replace: true });
    } finally {
      setCompleting(false);
    }
  };
  return <main className="onboarding-page">
    <header className="topbar onboarding-topbar"><div className="brand onboarding-brand"><img className="brand-icon" src="/risoto-app-icon.png" alt="" aria-hidden="true" />RISOTO<span>RISk Out Through Observation</span></div></header>
    <SetupPage companyRole="main" onboarding onCreated={complete} />
    {createdCompany && <AppNoticeDialog kicker="REALTIME COLLECTION" title="실시간 수집을 시작했습니다" onConfirm={acknowledgeCollectionStart} busy={completing}>
      <p><strong>{createdCompany.name}</strong>을(를) 나의 기업으로 등록했습니다.</p>
      <p className="app-notice-detail">등록과 동시에 최근 기사 수집과 실시간 모니터링이 시작됩니다. 요약 및 수집 현황에서 진행 상태를 확인할 수 있습니다.</p>
    </AppNoticeDialog>}
  </main>;
}

function CompanyEditModal({ companyId, onDirtyChange, onClose, onChanged }) {
  const [industries, setIndustries] = useState([]);
  const [selected, setSelected] = useState(null);
  const [form, setForm] = useState(() => companyToForm(null));
  const [initialForm, setInitialForm] = useState(() => companyToForm(null));
  const [keywordDrafts, setKeywordDrafts] = useState(() => emptyKeywordDrafts());
  const [formVersion, setFormVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState(null);
  const { confirm, confirmationDialog, confirming } = useAppConfirm();
  const submittedKeywords = companyKeywordsWithDrafts(form, keywordDrafts);
  const hasKeywordDraft = Object.values(keywordDrafts).some((value) => value.trim());
  const isDirty = Boolean(selected) && (companyFormSignature(form) !== companyFormSignature(initialForm) || hasKeywordDraft);
  const keywordCount = Object.values(submittedKeywords).reduce((sum, values) => sum + values.length, 0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([api.get("/industries"), api.get("/companies")]).then(([industryResponse, companyResponse]) => {
      if (!active) return;
      const nextCompany = companyResponse.data.find((company) => String(company.id) === String(companyId)) ?? null;
      const nextForm = companyToForm(nextCompany);
      setIndustries(industryResponse.data); setSelected(nextCompany); setForm(nextForm); setInitialForm(nextForm); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); setNotice(nextCompany ? null : { type: "error", message: "수정할 기업 정보를 찾지 못했습니다." });
    }).catch((error) => active && setNotice({ type: "error", message: getErrorMessage(error) })).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [companyId]);

  useEffect(() => { onDirtyChange(isDirty); return () => onDirtyChange(false); }, [isDirty, onDirtyChange]);
  useEffect(() => {
    if (!isDirty) return undefined;
    const warnBeforeUnload = (event) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [isDirty]);

  const requestClose = useCallback(async () => {
    if (confirming) return;
    if (isDirty) {
      const confirmed = await confirm({
        kicker: "UNSAVED CHANGES",
        title: "저장하지 않은 변경사항을 버리고 수정창을 닫을까요?",
        message: "닫으면 입력한 변경사항은 저장되지 않습니다.",
        confirmLabel: "닫기",
        tone: "danger",
      });
      if (!confirmed) return;
    }
    onClose();
  }, [confirm, confirming, isDirty, onClose]);
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event) => { if (event.key === "Escape") requestClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => { document.body.style.overflow = previousOverflow; window.removeEventListener("keydown", closeOnEscape); };
  }, [requestClose]);

  const changeField = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const changeKeyword = (field, values) => setForm((current) => ({ ...current, [field]: values }));
  const changeKeywordDraft = (field, value) => setKeywordDrafts((current) => ({ ...current, [field]: value }));
  const removeCompany = async () => {
    if (!selected || selected.company_role === "main" || deleting || saving) return;
    const confirmed = await confirm({
      kicker: "DELETE COMPETITOR",
      title: `${selected.name}을(를) 삭제할까요?`,
      detail: "수집 작업, 기사, 분석 결과와 위험 이벤트도 함께 삭제되며 복구할 수 없습니다.",
      confirmLabel: "삭제",
      tone: "danger",
    });
    if (!confirmed) return;
    setDeleting(true); setNotice(null);
    try {
      await api.delete(`/companies/${selected.id}`);
      onChanged();
      onClose();
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setDeleting(false); }
  };

  const save = async (event) => {
    event.preventDefault();
    const validationError = validateCompanyForm(form, keywordCount);
    if (!selected || validationError) { setNotice({ type: "error", message: validationError || "수정할 기업을 찾지 못했습니다." }); return; }
    const confirmed = await confirm({
      kicker: "SAVE CHANGES",
      title: `${selected.name}의 변경사항을 저장할까요?`,
      message: "기업 정보와 기사 수집 설정을 새 내용으로 반영합니다.",
      confirmLabel: "저장",
    });
    if (!confirmed) return;
    setSaving(true); setNotice(null);
    try {
      const response = await api.put(`/companies/${selected.id}`, companyPayload(form, submittedKeywords));
      const updated = response.data; const nextForm = companyToForm(updated);
      setSelected(updated); setForm(nextForm); setInitialForm(nextForm); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); setNotice({ type: "success", message: `${updated.name}의 기업 정보와 수집 설정을 저장했습니다.` }); onChanged();
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setSaving(false); }
  };

  const editTargetLabel = selected?.company_role === "main" ? "나의 기업" : COMPETITOR_LABEL;

  return <div className="company-edit-modal">
    <button className="company-edit-backdrop" type="button" onClick={requestClose} aria-label="기업 수정창 닫기" />
    <section className="company-edit-dialog" role="dialog" aria-modal="true" aria-labelledby="company-edit-title">
      <div className="company-edit-dialog-head"><div><span className="eyebrow">{selected?.company_role === "main" ? "EDIT MY COMPANY" : "EDIT COMPETITOR"}</span><h1 id="company-edit-title">{editTargetLabel} 수정</h1></div><button className="company-edit-close" type="button" onClick={requestClose} aria-label="수정창 닫기">×</button></div>
      {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
      {loading ? <p className="empty-state">기업 설정을 불러오는 중입니다.</p> : selected && <form className="edit-card" onSubmit={save}>
        <div className="card-heading"><div><div className="company-role-line"><span className={`company-role-badge ${selected.company_role}`}>{selected.company_role === "main" ? "나의 기업" : COMPETITOR_LABEL}</span><span className="eyebrow">EDIT COLLECTION CONTEXT</span></div><h2>{selected.name} 설정</h2></div></div>
        <CompanySettingsFields idPrefix="management" form={form} industries={industries} disabled={saving || deleting} version={`${selected.id}-${formVersion}`} onFieldChange={changeField} onKeywordChange={changeKeyword} onKeywordDraftChange={changeKeywordDraft} />
        <div className="edit-form-actions">{selected.company_role === "competitor" && <button className="delete-button" type="button" onClick={removeCompany} disabled={saving || deleting}><span>{deleting ? "삭제 중..." : `${COMPETITOR_LABEL} 삭제`}</span><b aria-hidden="true">×</b></button>}<button className="submit-button" type="submit" disabled={saving || deleting || !isDirty}><span>{saving ? "저장 중..." : "변경사항 저장"}</span><b aria-hidden="true">→</b></button></div>
      </form>}
    </section>
    {confirmationDialog}
  </div>
}

function CompanyRegistrationModal({ onClose, onCreated }) {
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => { document.body.style.overflow = previousOverflow; window.removeEventListener("keydown", closeOnEscape); };
  }, [onClose]);

  const complete = async (company) => {
    await onCreated(company);
    onClose();
  };

  return <div className="company-edit-modal">
    <button className="company-edit-backdrop" type="button" onClick={onClose} aria-label="비교 기업 등록창 닫기" />
    <section className="company-edit-dialog company-registration-dialog" role="dialog" aria-modal="true" aria-labelledby="company-registration-title">
      <div className="company-edit-dialog-head"><div><span className="eyebrow">NEW COMPETITOR TARGET</span><h1 id="company-registration-title">비교 기업 등록</h1></div><button className="company-edit-close" type="button" onClick={onClose} aria-label="등록창 닫기">×</button></div>
      <SetupPage companyRole="competitor" registrationOnly onCreated={complete} />
    </section>
  </div>;
}

export default function CompanyAdministrationPage({ onDirtyChange, onOpenCompany }) {
  const [editingCompanyId, setEditingCompanyId] = useState(null);
  const [registrationOpen, setRegistrationOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  return <div className="company-admin-page">
    <SetupPage companyRole="competitor" showRegistrationForm={false} onRegister={() => setRegistrationOpen(true)} onOpenCompany={onOpenCompany} onEditCompany={setEditingCompanyId} refreshKey={refreshKey} />
    {registrationOpen && <CompanyRegistrationModal onClose={() => setRegistrationOpen(false)} onCreated={() => setRefreshKey((current) => current + 1)} />}
    {editingCompanyId && <CompanyEditModal companyId={editingCompanyId} onDirtyChange={onDirtyChange} onClose={() => setEditingCompanyId(null)} onChanged={() => setRefreshKey((current) => current + 1)} />}
  </div>
}
