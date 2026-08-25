import { useCallback, useEffect, useMemo, useState } from "react";

import { api, getErrorMessage } from "../../api";
import {
  COMPANY_KEYWORD_FIELDS,
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
} from "./companyForm";

// 키워드 입력, 중복 방지, 칩 삭제를 처리하는 재사용 입력 컴포넌트다.
function KeywordInput({ id, label, usage, hint, values, onChange, onDraftChange, disabled = false }) {
  const [draft, setDraft] = useState("");
  const changeDraft = (value) => { setDraft(value); onDraftChange?.(value); };
  // 작성 중인 값을 정규화해 중복이 아닐 때만 키워드 목록에 추가한다.
  const addValue = () => {
    const normalized = draft.trim().replace(/\s+/g, " ");
    if (!normalized) return;
    if (!values.some((value) => value.toLocaleLowerCase() === normalized.toLocaleLowerCase())) {
      onChange([...values, normalized]);
    }
    changeDraft("");
  };

  return (
    <div>
      <label htmlFor={id} className="field-label field-label-with-note"><span>{label}</span>{usage && <small>{usage}</small>}</label>
      <div className="keyword-shell focus-within:ring-2 focus-within:ring-[#756e69]/25">
        {values.map((value) => (
          <span className="keyword-chip" key={value}>
            {value}<button type="button" aria-label={`${value} 삭제`} disabled={disabled} onClick={() => onChange(values.filter((item) => item !== value))}>×</button>
          </span>
        ))}
        <input id={id} value={draft} onChange={(event) => changeDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === ",") { event.preventDefault(); addValue(); }
            if (event.key === "Backspace" && !draft && values.length) onChange(values.slice(0, -1));
          }}
          onBlur={addValue} placeholder={values.length ? "추가 입력" : hint} autoComplete="off" maxLength={200} disabled={disabled} />
      </div>
      <p className="field-hint">Enter 또는 쉼표로 여러 항목을 추가할 수 있습니다.</p>
    </div>
  );
}

// 등록과 수정 화면이 동일하게 사용하는 기업 기본 정보와 키워드 입력 묶음이다.
function CompanySettingsFields({ idPrefix, form, industries, disabled, version, onFieldChange, onKeywordChange, onKeywordDraftChange }) {
  return <div className="edit-form-grid">
    <div><label className="field-label" htmlFor={`${idPrefix}-company-name`}>기업명</label><input className="text-field" id={`${idPrefix}-company-name`} value={form.name} disabled={disabled} onChange={(event) => onFieldChange("name", event.target.value)} placeholder="기업명" maxLength={200} required /></div>
    <div><label className="field-label field-label-with-note" htmlFor={`${idPrefix}-ticker`}><span>종목코드</span><small>상장기업</small></label><input className="text-field" id={`${idPrefix}-ticker`} value={form.ticker} disabled={disabled} onChange={(event) => onFieldChange("ticker", event.target.value)} placeholder="선택 입력" maxLength={30} /></div>
    <div className="edit-field-wide"><label className="field-label" htmlFor={`${idPrefix}-industry`}>산업군</label><select className="text-field" id={`${idPrefix}-industry`} value={form.industryId} disabled={disabled} onChange={(event) => onFieldChange("industryId", event.target.value)} required><option value="">산업군을 선택</option>{industries.map((industry) => <option value={industry.id} key={industry.id}>{industry.name}</option>)}</select></div>
    {COMPANY_KEYWORD_FIELDS.map(({ field, label, usage, hint }) => <KeywordInput key={`${idPrefix}-${version}-${field}`} id={`${idPrefix}-${field}-keywords`} label={label} usage={usage} hint={hint} values={form[field]} onChange={(values) => onKeywordChange(field, values)} onDraftChange={(value) => onKeywordDraftChange(field, value)} disabled={disabled} />)}
  </div>;
}

// 기업의 키워드와 모니터링 상태를 요약 카드로 표시한다.
export function CompanyCard({ company, monitoringSummary, onOpen, onEdit }) {
  // API의 평면 키워드 목록을 카드 영역별 배열로 한 번만 재구성한다.
  const grouped = useMemo(() => {
    const result = { alias: [], peer: [], product: [], risk: [] };
    company.keywords.forEach((keyword) => result[keyword.keyword_type]?.push(keyword.value));
    return result;
  }, [company]);
  return (
    <article className="company-card">
      <div className="company-card-head flex items-start justify-between gap-5">
        <div><span className="eyebrow">{company.readiness_status === "active" ? "ACTIVE MONITOR" : "DATA PREPARATION"}</span><h3><button className="company-name-link" type="button" onClick={() => onOpen(company.id)}>{company.name}</button></h3><p>{company.industry_name}</p></div>
        {onEdit && <div className="company-card-controls"><button type="button" onClick={() => onEdit(company.id)}>설정 수정</button></div>}
      </div>
      <div className="company-card-keywords mt-6 grid gap-4 sm:grid-cols-2">
        {Object.entries(KEYWORD_LABELS).map(([type, label]) => <div key={type}>
          <span className="mini-label">{label}</span><p className="mt-1 text-sm leading-6 text-[#4e4642]">{grouped[type].join(" · ") || "등록 없음"}</p>
        </div>)}
      </div>
      <div className={`pipeline-state ${company.monitoring_status}`}>
        <strong>{READINESS_LABELS[company.readiness_status] ?? MONITORING_LABELS[company.monitoring_status] ?? company.monitoring_status}</strong>
        <span>{company.readiness_status === "active" ? `기사 ${formatNumber(company.accepted_article_count)}건 · 유효 구간 ${formatNumber(company.valid_nonempty_window_count)}개` : `기사 ${formatNumber(company.accepted_article_count)}/50 · 유효 구간 ${formatNumber(company.valid_nonempty_window_count)}/40`}</span>
        {monitoringSummary && <small>수집 {monitoringSummary.article_count}건 · 분석 {monitoringSummary.analyzed_count}건</small>}
      </div>
    </article>
  );
}

// 모니터링 대상 기업 등록 폼과 기존 기업 목록을 관리한다.
function SetupPage({ onOpenCompany, onEditCompany }) {
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

  const changeField = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const changeKeyword = (field, values) => setForm((current) => ({ ...current, [field]: values }));
  const changeKeywordDraft = (field, value) => setKeywordDrafts((current) => ({ ...current, [field]: value }));

  // 산업·기업·모니터링 요약을 함께 불러와 등록 화면 상태를 갱신한다.
  const loadData = useCallback(async () => {
    try {
      const [industryResponse, companyResponse] = await Promise.all([api.get("/industries"), api.get("/companies")]);
      setIndustries(industryResponse.data); setCompanies(companyResponse.data);
      const results = await Promise.allSettled(companyResponse.data.map((company) => api.get(`/companies/${company.id}/monitoring`)));
      setMonitoringSummaries(Object.fromEntries(results.flatMap((result, index) => result.status === "fulfilled" ? [[companyResponse.data[index].id, result.value.data]] : [])));
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setLoading(false); }
  }, []);

  // 최초 진입과 이후 30초마다 등록 기업 및 진행 상태를 동기화한다.
  useEffect(() => { loadData(); const timer = window.setInterval(loadData, 30000); return () => window.clearInterval(timer); }, [loadData]);

  // 입력값을 검증해 기업과 키워드를 등록하고 최신 목록을 다시 불러온다.
  const submit = async (event) => {
    event.preventDefault();
    if (!form.name.trim() || !form.industryId) { setNotice({ type: "error", message: "기업명과 산업군을 입력해 주세요." }); return; }
    if (keywordCount > 100) { setNotice({ type: "error", message: "키워드는 전체 100개까지 저장할 수 있습니다." }); return; }
    setSubmitting(true); setNotice(null);
    try {
      const response = await api.post("/companies", { name: form.name.trim(), ticker: form.ticker.trim().toUpperCase() || null, industry_id: Number(form.industryId), backfill_days: 7,
        keywords: companyKeywordPayload(submittedKeywords) });
      setForm(companyToForm(null)); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); await loadData();
      setNotice({ type: "success", message: response.data.is_existing ? `${response.data.name}에 새 키워드 ${response.data.added_keyword_count}개를 반영했습니다.` : `${response.data.name}의 과거·실시간 수집을 시작했습니다.` });
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setSubmitting(false); }
  };

  return <>
    <section className="hero-grid">
      <div className="hero-copy"><span className="eyebrow">MONITOR SETUP / 01</span><h1>분석할 기업의<br /><em>정보</em>를 입력하세요.</h1>
        <p>기업명과 별칭은 대상을 식별하고, 제품·브랜드와 키워드는 기사 수집·관련성 판별에, 유사기업은 대응책 생성에 활용합니다.</p>
        <div className="sequence"><span className="active">01 기업 정보 입력</span><span>02 기사 수집·판별</span><span>03 이슈 탐지·대응</span></div>
      </div>
      <form className="setup-card" onSubmit={submit}>
        <div className="card-heading"><div><span className="eyebrow">NEW TARGET</span><h2>기업 등록</h2></div><span className="step-number">01</span></div>
        <CompanySettingsFields idPrefix="register" form={form} industries={industries} disabled={submitting || loading} version={formVersion} onFieldChange={changeField} onKeywordChange={changeKeyword} onKeywordDraftChange={changeKeywordDraft} />
        <div className="settings-note"><strong>수집 문맥</strong><span>별칭·제품·키워드는 기사 수집·판별에, 유사기업은 대응책 생성에 사용됩니다.</span><small>키워드 {keywordCount}/100</small></div>
        {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
        <button className="submit-button" type="submit" disabled={submitting || loading}><span>{submitting ? "등록 중..." : "모니터링 대상 등록"}</span><b aria-hidden="true">→</b></button>
      </form>
    </section>
    <section className="registered-section"><div className="section-title"><div><span className="eyebrow">REGISTERED TARGETS</span><h2>등록한 기업</h2></div><strong>{companies.length.toString().padStart(2, "0")}</strong></div>
      {loading ? <p className="empty-state">기업 정보를 불러오는 중입니다.</p> : companies.length ? <div className="company-list">{companies.map((company) => <CompanyCard company={company} key={company.id} monitoringSummary={monitoringSummaries[company.id]} onOpen={onOpenCompany} onEdit={onEditCompany} />)}</div> : <p className="empty-state">아직 등록한 기업이 없습니다.</p>}
    </section>
  </>;
}

// 등록된 기업을 선택해 기본 정보와 수집 키워드 전체를 수정한다.
function CompanyManagementPage({ initialCompanyId, onDirtyChange, onOpenCompany }) {
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

  // 화면 진입 시 목록을 한 번만 불러와 작성 중인 폼이 주기 갱신으로 덮이지 않게 한다.
  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([api.get("/industries"), api.get("/companies")])
      .then(([industryResponse, companyResponse]) => {
        if (!active) return;
        const nextCompanies = companyResponse.data;
        const requestedId = initialCompanyId ? String(initialCompanyId) : "";
        const nextCompany = nextCompanies.find((company) => String(company.id) === requestedId) ?? nextCompanies[0] ?? null;
        const nextForm = companyToForm(nextCompany);
        setIndustries(industryResponse.data);
        setCompanies(nextCompanies);
        setSelectedId(nextCompany ? String(nextCompany.id) : "");
        setForm(nextForm);
        setInitialForm(nextForm);
        setKeywordDrafts(emptyKeywordDrafts());
        setFormVersion((current) => current + 1);
        setNotice(null);
      })
      .catch((error) => active && setNotice({ type: "error", message: getErrorMessage(error) }))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [initialCompanyId]);

  // 최상위 내비게이션과 브라우저 종료가 저장하지 않은 변경을 확인할 수 있게 알린다.
  useEffect(() => { onDirtyChange(isDirty); return () => onDirtyChange(false); }, [isDirty, onDirtyChange]);
  useEffect(() => {
    if (!isDirty) return undefined;
    const warnBeforeUnload = (event) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [isDirty]);

  // 기업 선택을 바꿀 때 미저장 입력이 있으면 먼저 사용자 확인을 받는다.
  const changeCompany = (nextId) => {
    if (nextId === selectedId) return;
    if (isDirty && !window.confirm("저장하지 않은 변경사항을 버리고 다른 기업을 선택할까요?")) return;
    const nextCompany = companies.find((company) => String(company.id) === nextId) ?? null;
    const nextForm = companyToForm(nextCompany);
    setSelectedId(nextId);
    setForm(nextForm);
    setInitialForm(nextForm);
    setKeywordDrafts(emptyKeywordDrafts());
    setFormVersion((current) => current + 1);
    setNotice(null);
  };

  const changeField = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const changeKeyword = (field, values) => setForm((current) => ({ ...current, [field]: values }));
  const changeKeywordDraft = (field, value) => setKeywordDrafts((current) => ({ ...current, [field]: value }));
  const resetForm = () => { setForm(initialForm); setKeywordDrafts(emptyKeywordDrafts()); setFormVersion((current) => current + 1); setNotice(null); };

  // 기업 삭제 전에는 수집한 기사와 분석 결과도 함께 제거된다는 점을 명확히 확인한다.
  const removeCompany = async () => {
    if (!selected || deleting || saving) return;
    const confirmed = window.confirm(`${selected.name}을(를) 삭제할까요?\n수집 작업, 기사, 필터·분석 결과 및 위험 이벤트도 함께 삭제되며 복구할 수 없습니다.`);
    if (!confirmed) return;
    setDeleting(true); setNotice(null);
    try {
      await api.delete(`/companies/${selected.id}`);
      const nextCompanies = companies.filter((company) => company.id !== selected.id);
      const nextCompany = nextCompanies[0] ?? null;
      const nextForm = companyToForm(nextCompany);
      setCompanies(nextCompanies);
      setSelectedId(nextCompany ? String(nextCompany.id) : "");
      setForm(nextForm); setInitialForm(nextForm);
      setKeywordDrafts(emptyKeywordDrafts());
      setFormVersion((current) => current + 1);
      setNotice({ type: "success", message: `${selected.name}과(와) 관련 수집·분석 자료를 삭제했습니다.` });
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setDeleting(false); }
  };

  // 현재 폼을 전체 설정 교체 요청으로 보내 키워드 추가와 삭제를 동시에 반영한다.
  const save = async (event) => {
    event.preventDefault();
    if (!selectedId || !form.name.trim() || !form.industryId) {
      setNotice({ type: "error", message: "기업명과 산업군을 입력해 주세요." });
      return;
    }
    if (keywordCount > 100) {
      setNotice({ type: "error", message: "키워드는 전체 100개까지 저장할 수 있습니다." });
      return;
    }
    setSaving(true); setNotice(null);
    try {
      const response = await api.put(`/companies/${selectedId}`, {
        name: form.name.trim(),
        ticker: form.ticker.trim() || null,
        industry_id: Number(form.industryId),
        keywords: companyKeywordPayload(submittedKeywords),
      });
      const updated = response.data;
      const nextForm = companyToForm(updated);
      setCompanies((current) => current.map((company) => company.id === updated.id ? updated : company));
      setForm(nextForm); setInitialForm(nextForm);
      setKeywordDrafts(emptyKeywordDrafts());
      setFormVersion((current) => current + 1);
      setNotice({ type: "success", message: `${updated.name}의 수집 설정을 저장했습니다.${updated.added_keyword_count ? " 수집에 사용하는 새 키워드는 최근 7일 자료도 보강합니다." : ""}` });
    } catch (error) { setNotice({ type: "error", message: getErrorMessage(error) }); }
    finally { setSaving(false); }
  };

  return <section className="workspace management-workspace">
    <div className="workspace-head"><div><span className="eyebrow">TARGET SETTINGS / 02</span><h1>기업 관리</h1><p>기업 정보와 검색 문맥을 수정합니다. 과거 수집 이력은 그대로 보존됩니다.</p></div></div>
    {notice && <div className={`notice ${notice.type}`} role="status">{notice.message}</div>}
    {loading ? <p className="empty-state">기업 설정을 불러오는 중입니다.</p> : !companies.length ? <p className="empty-state">수정할 기업이 없습니다. 먼저 기업을 등록해 주세요.</p> : <>
      <div className="management-selector"><label htmlFor="management-company">수정할 기업</label><select id="management-company" value={selectedId} disabled={saving || deleting} onChange={(event) => changeCompany(event.target.value)}>{companies.map((company) => <option value={company.id} key={company.id}>{company.name} · {company.industry_name}</option>)}</select>{selected && <button type="button" disabled={deleting} onClick={() => onOpenCompany(selected.id)}>실시간 현황 보기</button>}</div>
      <form className="edit-card" onSubmit={save}>
        <div className="card-heading"><div><span className="eyebrow">EDIT COLLECTION CONTEXT</span><h2>{selected?.name} 설정</h2></div><span className="step-number">02</span></div>
        <CompanySettingsFields idPrefix="management" form={form} industries={industries} disabled={saving || deleting} version={`${selectedId}-${formVersion}`} onFieldChange={changeField} onKeywordChange={changeKeyword} onKeywordDraftChange={changeKeywordDraft} />
        <div className="settings-note"><strong>적용 범위</strong><span>삭제한 키워드는 다음 수집부터 제외됩니다. 이미 수집된 기사와 분석 결과는 감사 이력으로 남습니다.</span><small>키워드 {keywordCount}/100</small></div>
        <div className="edit-form-actions"><button className="delete-button" type="button" onClick={removeCompany} disabled={saving || deleting}><span>{deleting ? "삭제 중..." : "기업 삭제"}</span><b aria-hidden="true">×</b></button><button className="secondary-button" type="button" onClick={resetForm} disabled={saving || deleting || !isDirty}>초기화</button><button className="submit-button" type="submit" disabled={saving || deleting || !isDirty}><span>{saving ? "저장 중..." : "변경사항 저장"}</span><b aria-hidden="true">→</b></button></div>
      </form>
    </>}
  </section>;
}

// 기업 관리와 등록 작업을 한 메뉴 안에서 전환한다.
export default function CompanyAdministrationPage({ mode, onModeChange, initialCompanyId, onDirtyChange, onOpenCompany, onEditCompany }) {
  return <div className="company-admin-page">
    <div className="admin-page-tabs" role="tablist" aria-label="기업 관리 작업"><button type="button" role="tab" aria-selected={mode === "edit"} className={mode === "edit" ? "active" : ""} onClick={() => onModeChange("edit")}>기업 관리</button><button type="button" role="tab" aria-selected={mode === "register"} className={mode === "register" ? "active" : ""} onClick={() => onModeChange("register")}>기업 등록</button></div>
    {mode === "register" ? <SetupPage onOpenCompany={onOpenCompany} onEditCompany={onEditCompany} /> : <CompanyManagementPage initialCompanyId={initialCompanyId} onDirtyChange={onDirtyChange} onOpenCompany={onOpenCompany} />}
  </div>;
}
