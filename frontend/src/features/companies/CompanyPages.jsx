import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { api, getErrorMessage } from "../../api";
import {
  COMPANY_KEYWORD_FIELDS,
  COMPANY_SIZE_LABELS,
  KEYWORD_LABELS,
  MONITORING_LABELS,
  READINESS_LABELS,
  formatNumber,
} from "../../shared/presentation";
import {
  companyFormSignature,
  companyKeywordPayload,
  companyKeywordsWithDrafts,
  companyToForm,
  emptyKeywordDrafts,
  isValidAnnualRevenue,
} from "./companyForm";

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

export function CompanyCard({ company, monitoringSummary, onOpen, onEdit, competitorCompanyLabel = "경쟁사" }) {
  const grouped = useMemo(() => {
    const result = { alias: [], product: [], risk: [] };
    company.keywords?.forEach((keyword) => result[keyword.keyword_type]?.push(keyword.value));
    return result;
  }, [company]);
  const roleLabel = company.company_role === "main" ? "메인 기업" : competitorCompanyLabel;

  return <article className={`company-card ${company.company_role === "main" ? "main-company-card" : ""}`}>
    <div className="company-card-head flex items-start justify-between gap-5">
      <div><div className="company-role-line"><span className={`company-role-badge ${company.company_role}`}>{roleLabel}</span><span className="eyebrow">{company.readiness_status === "active" ? "ACTIVE MONITOR" : "DATA PREPARATION"}</span></div><h3><button className="company-name-link" type="button" onClick={() => onOpen(company.id)}>{company.name}</button></h3><p>{company.industry_name}</p></div>
      {onEdit && <div className="company-card-controls"><button type="button" onClick={() => onEdit(company.id)}>설정 수정</button></div>}
    </div>
    <div className="company-finance-summary"><div><span>연매출</span><strong>{formatRevenue(company.annual_revenue_100m_krw)}</strong></div><div><span>기업 규모</span><strong>{COMPANY_SIZE_LABELS[company.company_size_class] ?? "미입력"}</strong></div></div>
    <div className="company-card-keywords mt-6 grid gap-4 sm:grid-cols-3">{Object.entries(KEYWORD_LABELS).map(([type, label]) => <div key={type}><span className="mini-label">{label}</span><p className="mt-1 text-sm leading-6 text-[#4e4642]">{grouped[type].join(" · ") || "등록 없음"}</p></div>)}</div>
    <div className={`pipeline-state ${company.monitoring_status}`}><strong>{READINESS_LABELS[company.readiness_status] ?? MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</strong><span>{company.readiness_status === "active" ? `기사 ${formatNumber(company.accepted_article_count)}건 · 유효 구간 ${formatNumber(company.valid_nonempty_window_count)}개` : `기사 ${formatNumber(company.accepted_article_count)}/50 · 유효 구간 ${formatNumber(company.valid_nonempty_window_count)}/40`}</span>{monitoringSummary && <small>수집 {monitoringSummary.article_count}건 · 분석 {monitoringSummary.analyzed_count}건</small>}</div>
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

function SetupPage({ companyRole = "competitor", competitorCompanyLabel = "경쟁사", onCreated, onOpenCompany, onEditCompany, onboarding = false }) {
  const [industries, setIndustries] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [form, setForm] = useState(() => companyToForm(null));
  const [keywordDrafts, setKeywordDrafts] = useState(() => emptyKeywordDrafts());
  const [formVersion, setFormVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState(null);
  const [monitoringSummaries, setMonitoringSummaries] = useState({});
  const submittedKeywords = companyKeywordsWithDrafts(form, keywordDrafts);
  const keywordCount = Object.values(submittedKeywords).reduce((sum, values) => sum + values.length, 0);
  const targetLabel = companyRole === "main" ? "메인 기업" : competitorCompanyLabel;

  const changeField = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const changeKeyword = (field, values) => setForm((current) => ({ ...current, [field]: values }));
  const changeKeywordDraft = (field, value) => setKeywordDrafts((current) => ({ ...current, [field]: value }));

  const loadData = useCallback(async () => {
    try {
      const industryResponse = await api.get("/industries");
      setIndustries(industryResponse.data);
      if (!onboarding) {
        const companyResponse = await api.get("/companies");
        const competitorCompanies = companyResponse.data.filter((company) => company.company_role === "competitor");
        setCompanies(competitorCompanies);
        const results = await Promise.allSettled(competitorCompanies.map((company) => api.get(`/companies/${company.id}/monitoring`)));
        setMonitoringSummaries(Object.fromEntries(results.flatMap((result, index) => result.status === "fulfilled" ? [[competitorCompanies[index].id, result.value.data]] : [])));
      }
      setNotice(null);
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setLoading(false); }
  }, [onboarding]);

  useEffect(() => {
    loadData();
    if (onboarding) return undefined;
    const timer = window.setInterval(loadData, 30000);
    return () => window.clearInterval(timer);
  }, [loadData, onboarding]);

  const submit = async (event) => {
    event.preventDefault();
    const validationError = validateCompanyForm(form, keywordCount);
    if (validationError) { setNotice({ type: "error", message: validationError }); return; }
    setSubmitting(true); setNotice(null);
    try {
      const endpoint = companyRole === "main" ? "/companies/main" : "/companies";
      const response = await api.post(endpoint, { ...companyPayload(form, submittedKeywords), backfill_days: 7 });
      if (onCreated) await onCreated(response.data);
      if (!onboarding) {
        setForm(companyToForm(null)); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); await loadData();
        setNotice({ type: "success", message: `${response.data.name}을(를) ${targetLabel}(으)로 등록하고 수집을 시작했습니다.` });
      }
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); setSubmitting(false); }
  };

  return <>
    <section className={`hero-grid ${onboarding ? "onboarding-hero" : ""}`}>
      <div className="hero-copy"><span className="eyebrow">{companyRole === "main" ? "MAIN COMPANY / 01" : "COMPETITOR / 01"}</span><h1>{companyRole === "main" ? <>가장 먼저,<br /><em>메인 기업</em>을 등록하세요.</> : <>분석할 <em>{competitorCompanyLabel}</em>의<br />정보를 입력하세요.</>}</h1><p>{companyRole === "main" ? "메인 기업은 워크스페이스의 위험 대응 기준이 됩니다. 등록 후 정보는 수정할 수 있지만 삭제하거나 역할을 바꿀 수 없습니다." : `${competitorCompanyLabel}의 위험은 메인 기업에 미칠 수 있는 영향과 대응 경우의 수로 분석됩니다.`}</p><div className="sequence"><span className="active">01 기업 정보 입력</span><span>02 기사 수집·판별</span><span>03 이슈 탐지·대응</span></div></div>
      <form className="setup-card" onSubmit={submit}>
        <div className="card-heading"><div><span className="eyebrow">NEW {companyRole === "main" ? "MAIN" : "COMPETITOR"} TARGET</span><h2>{targetLabel} 등록</h2></div><span className="step-number">01</span></div>
        <CompanySettingsFields idPrefix={companyRole === "main" ? "main-register" : "competitor-register"} form={form} industries={industries} disabled={submitting || loading} version={formVersion} onFieldChange={changeField} onKeywordChange={changeKeyword} onKeywordDraftChange={changeKeywordDraft} />
        <div className="settings-note"><strong>수집 문맥</strong><span>별칭·제품·위험 키워드는 기사 수집과 관련성 판별에 사용됩니다.</span><small>키워드 {keywordCount}/100</small></div>
        {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
        <button className="submit-button" type="submit" disabled={submitting || loading}><span>{submitting ? "등록 중..." : `${targetLabel} 등록`}</span><b aria-hidden="true">→</b></button>
      </form>
    </section>
    {!onboarding && <section className="registered-section"><div className="section-title"><div><span className="eyebrow">REGISTERED COMPETITORS</span><h2>등록한 {competitorCompanyLabel}</h2></div><strong>{companies.length.toString().padStart(2, "0")}</strong></div>{loading ? <p className="empty-state">기업 정보를 불러오는 중입니다.</p> : companies.length ? <div className="company-list">{companies.map((company) => <CompanyCard company={company} key={company.id} monitoringSummary={monitoringSummaries[company.id]} onOpen={onOpenCompany} onEdit={onEditCompany} competitorCompanyLabel={competitorCompanyLabel} />)}</div> : <p className="empty-state">아직 등록한 {competitorCompanyLabel}가 없습니다.</p>}</section>}
  </>;
}

export function MainCompanyOnboardingPage({ onCreated }) {
  const navigate = useNavigate();
  useEffect(() => { document.title = "RISOTO · 메인 기업 등록"; }, []);
  const complete = async () => { await onCreated(); navigate("/main", { replace: true }); };
  return <main className="onboarding-page"><header className="onboarding-topbar"><div className="login-brand"><img src="/risoto-app-icon.png" alt="" aria-hidden="true" /><span>RISOTO</span></div><small>메인 기업을 등록하면 워크스페이스가 열립니다.</small></header><SetupPage companyRole="main" onboarding onCreated={complete} /></main>;
}

function CompanyManagementPage({ initialCompanyId, onDirtyChange, onOpenCompany, competitorCompanyLabel }) {
  const [industries, setIndustries] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState(() => companyToForm(null));
  const [initialForm, setInitialForm] = useState(() => companyToForm(null));
  const [keywordDrafts, setKeywordDrafts] = useState(() => emptyKeywordDrafts());
  const [formVersion, setFormVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState(null);
  const selected = companies.find((company) => String(company.id) === selectedId) ?? null;
  const submittedKeywords = companyKeywordsWithDrafts(form, keywordDrafts);
  const hasKeywordDraft = Object.values(keywordDrafts).some((value) => value.trim());
  const isDirty = Boolean(selectedId) && (companyFormSignature(form) !== companyFormSignature(initialForm) || hasKeywordDraft);
  const keywordCount = Object.values(submittedKeywords).reduce((sum, values) => sum + values.length, 0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([api.get("/industries"), api.get("/companies")]).then(([industryResponse, companyResponse]) => {
      if (!active) return;
      const nextCompanies = [...companyResponse.data].sort((a, b) => (a.company_role === "main" ? -1 : 1) - (b.company_role === "main" ? -1 : 1));
      const requestedId = initialCompanyId ? String(initialCompanyId) : "";
      const nextCompany = nextCompanies.find((company) => String(company.id) === requestedId) ?? nextCompanies[0] ?? null;
      const nextForm = companyToForm(nextCompany);
      setIndustries(industryResponse.data); setCompanies(nextCompanies); setSelectedId(nextCompany ? String(nextCompany.id) : ""); setForm(nextForm); setInitialForm(nextForm); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); setNotice(null);
    }).catch((error) => active && setNotice({ type: "error", message: getErrorMessage(error) })).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [initialCompanyId]);

  useEffect(() => { onDirtyChange(isDirty); return () => onDirtyChange(false); }, [isDirty, onDirtyChange]);
  useEffect(() => {
    if (!isDirty) return undefined;
    const warnBeforeUnload = (event) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [isDirty]);

  const selectCompany = (nextId) => {
    if (nextId === selectedId || (isDirty && !window.confirm("저장하지 않은 변경사항을 버리고 다른 기업을 선택할까요?"))) return;
    const nextCompany = companies.find((company) => String(company.id) === nextId) ?? null;
    const nextForm = companyToForm(nextCompany);
    setSelectedId(nextId); setForm(nextForm); setInitialForm(nextForm); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); setNotice(null);
  };
  const changeField = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const changeKeyword = (field, values) => setForm((current) => ({ ...current, [field]: values }));
  const changeKeywordDraft = (field, value) => setKeywordDrafts((current) => ({ ...current, [field]: value }));
  const resetForm = () => { setForm(initialForm); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); setNotice(null); };

  const removeCompany = async () => {
    if (!selected || selected.company_role === "main" || deleting || saving) return;
    if (!window.confirm(`${selected.name}을(를) 삭제할까요?\n수집 작업, 기사, 분석 결과와 위험 이벤트도 함께 삭제되며 복구할 수 없습니다.`)) return;
    setDeleting(true); setNotice(null);
    try {
      await api.delete(`/companies/${selected.id}`);
      const nextCompanies = companies.filter((company) => company.id !== selected.id);
      const nextCompany = nextCompanies[0] ?? null; const nextForm = companyToForm(nextCompany);
      setCompanies(nextCompanies); setSelectedId(nextCompany ? String(nextCompany.id) : ""); setForm(nextForm); setInitialForm(nextForm); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); setNotice({ type: "success", message: `${selected.name}과(와) 관련 수집·분석 자료를 삭제했습니다.` });
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setDeleting(false); }
  };

  const save = async (event) => {
    event.preventDefault();
    const validationError = validateCompanyForm(form, keywordCount);
    if (!selectedId || validationError) { setNotice({ type: "error", message: validationError || "수정할 기업을 선택해 주세요." }); return; }
    setSaving(true); setNotice(null);
    try {
      const response = await api.put(`/companies/${selectedId}`, companyPayload(form, submittedKeywords));
      const updated = response.data; const nextForm = companyToForm(updated);
      setCompanies((current) => current.map((company) => company.id === updated.id ? updated : company)); setForm(nextForm); setInitialForm(nextForm); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); setNotice({ type: "success", message: `${updated.name}의 기업 정보와 수집 설정을 저장했습니다.` });
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setSaving(false); }
  };

  return <section className="workspace management-workspace">
    <div className="workspace-head"><div><span className="eyebrow">TARGET SETTINGS / 02</span><h1>기업 관리</h1><p>메인 기업과 {competitorCompanyLabel}의 정보 및 검색 문맥을 수정합니다.</p></div></div>
    {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
    {loading ? <p className="empty-state">기업 설정을 불러오는 중입니다.</p> : <>
      <div className="management-selector"><label htmlFor="management-company">수정할 기업</label><select id="management-company" value={selectedId} disabled={saving || deleting} onChange={(event) => selectCompany(event.target.value)}>{companies.map((company) => <option value={company.id} key={company.id}>{company.company_role === "main" ? "[메인 기업]" : `[${competitorCompanyLabel}]`} {company.name} · {company.industry_name}</option>)}</select>{selected && <button type="button" disabled={deleting} onClick={() => onOpenCompany(selected.id)}>실시간 현황 보기</button>}</div>
      {selected && <form className="edit-card" onSubmit={save}>
        <div className="card-heading"><div><div className="company-role-line"><span className={`company-role-badge ${selected.company_role}`}>{selected.company_role === "main" ? "메인 기업" : competitorCompanyLabel}</span><span className="eyebrow">EDIT COLLECTION CONTEXT</span></div><h2>{selected.name} 설정</h2></div><span className="step-number">02</span></div>
        <CompanySettingsFields idPrefix="management" form={form} industries={industries} disabled={saving || deleting} version={`${selectedId}-${formVersion}`} onFieldChange={changeField} onKeywordChange={changeKeyword} onKeywordDraftChange={changeKeywordDraft} />
        <div className="settings-note"><strong>적용 범위</strong><span>{selected.company_role === "main" ? "메인 기업은 수정할 수 있지만 삭제하거나 역할을 변경할 수 없습니다." : "삭제한 키워드는 다음 수집부터 제외되며 기존 분석 결과는 감사 이력으로 남습니다."}</span><small>키워드 {keywordCount}/100</small></div>
        <div className="edit-form-actions">{selected.company_role !== "main" && <button className="delete-button" type="button" onClick={removeCompany} disabled={saving || deleting}><span>{deleting ? "삭제 중..." : `${competitorCompanyLabel} 삭제`}</span><b aria-hidden="true">×</b></button>}<button className="secondary-button" type="button" onClick={resetForm} disabled={saving || deleting || !isDirty}>초기화</button><button className="submit-button" type="submit" disabled={saving || deleting || !isDirty}><span>{saving ? "저장 중..." : "변경사항 저장"}</span><b aria-hidden="true">→</b></button></div>
      </form>}
    </>}
  </section>;
}

export default function CompanyAdministrationPage({ mode, onModeChange, initialCompanyId, onDirtyChange, onOpenCompany, onEditCompany, competitorCompanyLabel = "경쟁사" }) {
  return <div className="company-admin-page">
    <div className="admin-page-tabs" role="tablist" aria-label="기업 관리 작업"><button type="button" role="tab" aria-selected={mode === "edit"} className={mode === "edit" ? "active" : ""} onClick={() => onModeChange("edit")}>기업 관리</button><button type="button" role="tab" aria-selected={mode === "register"} className={mode === "register" ? "active" : ""} onClick={() => onModeChange("register")}>{competitorCompanyLabel} 등록</button></div>
    {mode === "register" ? <SetupPage companyRole="competitor" competitorCompanyLabel={competitorCompanyLabel} onOpenCompany={onOpenCompany} onEditCompany={onEditCompany} /> : <CompanyManagementPage initialCompanyId={initialCompanyId} onDirtyChange={onDirtyChange} onOpenCompany={onOpenCompany} competitorCompanyLabel={competitorCompanyLabel} />}
  </div>;
}
