import { useEffect, useMemo, useState } from "react";

import { api, getErrorMessage } from "../../api";

export default function MyPage({ session, onAccountDeleted }) {
  const [companies, setCompanies] = useState([]);
  const [companyLoading, setCompanyLoading] = useState(true);
  const [companyError, setCompanyError] = useState(null);
  const [newPassword, setNewPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  useEffect(() => {
    let active = true;
    api.get("/companies")
      .then((response) => {
        if (!active) return;
        setCompanies(response.data ?? []);
        setCompanyError(null);
      })
      .catch((error) => {
        if (active) setCompanyError(getErrorMessage(error));
      })
      .finally(() => {
        if (active) setCompanyLoading(false);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!deleteOpen) return undefined;
    const closeWithEscape = (event) => {
      if (event.key === "Escape" && !deleteSubmitting) setDeleteOpen(false);
    };
    document.addEventListener("keydown", closeWithEscape);
    return () => document.removeEventListener("keydown", closeWithEscape);
  }, [deleteOpen, deleteSubmitting]);

  const mainCompany = useMemo(
    () => companies.find((company) => company.company_role === "main"),
    [companies],
  );
  const passwordsMatch = newPassword === passwordConfirmation;

  const submitPassword = async (event) => {
    event.preventDefault();
    setNotice(null);
    if (!passwordsMatch) {
      setNotice({ type: "error", message: "새 비밀번호와 비밀번호 확인이 일치하지 않습니다." });
      return;
    }

    setSubmitting(true);
    try {
      await api.put("/auth/password", {
        new_password: newPassword,
        new_password_confirmation: passwordConfirmation,
      });
      setNewPassword("");
      setPasswordConfirmation("");
      setNotice({ type: "success", message: "비밀번호가 변경되었습니다." });
    } catch (error) {
      setNotice({ type: "error", message: getErrorMessage(error) });
    } finally {
      setSubmitting(false);
    }
  };

  const openDeleteModal = () => {
    setDeletePassword("");
    setDeleteError(null);
    setDeleteOpen(true);
  };

  const closeDeleteModal = () => {
    if (deleteSubmitting) return;
    setDeleteOpen(false);
    setDeletePassword("");
    setDeleteError(null);
  };

  const submitAccountDeletion = async (event) => {
    event.preventDefault();
    setDeleteSubmitting(true);
    setDeleteError(null);
    try {
      await api.delete("/auth/account", {
        data: { current_password: deletePassword },
      });
      onAccountDeleted();
    } catch (error) {
      setDeleteError(getErrorMessage(error));
      setDeleteSubmitting(false);
    }
  };

  return <section className="workspace my-page">
    <div className="my-page-shell">
    <header className="workspace-head my-page-head">
      <div>
        <span className="eyebrow">ACCOUNT SETTINGS</span>
        <h1>마이페이지</h1>
        <p>계정 정보를 확인하고 로그인 비밀번호를 변경할 수 있습니다.</p>
      </div>
    </header>

    <div className="my-page-grid">
      <article className="edit-card account-settings-card">
      <section className="account-settings-pane account-summary-card">
        <div className="my-card-head">
          <div>
            <span className="eyebrow">PROFILE</span>
            <h2>계정 정보</h2>
          </div>
        </div>
        <dl className="account-readonly-list">
          <div>
            <dt>계정</dt>
            <dd>{session.user.email}</dd>
            <small>로그인에 사용하는 이메일 계정입니다.</small>
          </div>
          <div>
            <dt>나의 기업</dt>
            <dd>
              {companyLoading && "불러오는 중..."}
              {!companyLoading && companyError && "확인할 수 없음"}
              {!companyLoading && !companyError && (mainCompany?.name ?? "등록된 나의 기업 없음")}
            </dd>
            <small>{companyError ?? "마이페이지에서는 나의 기업을 변경할 수 없습니다."}</small>
          </div>
        </dl>
        {session.user.role === "general" && <div className="account-delete-row">
          <button className="submit-button account-delete-button" type="button" onClick={openDeleteModal}>
            <span>회원 탈퇴</span><b aria-hidden="true">→</b>
          </button>
        </div>}
      </section>

      <section className="account-settings-pane password-card">
        <div className="my-card-head">
          <div>
            <span className="eyebrow">SECURITY</span>
            <h2>비밀번호 변경</h2>
          </div>
        </div>
        <p className="password-card-description">현재 비밀번호 확인 없이 새 비밀번호를 바로 설정합니다.</p>
        <form className="password-change-form" onSubmit={submitPassword}>
          <label htmlFor="new-password">새 비밀번호</label>
          <div className="my-password-field">
            <input id="new-password" type={showPasswords ? "text" : "password"} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={8} maxLength={128} autoComplete="new-password" placeholder="8자 이상 입력" required />
            <button type="button" onClick={() => setShowPasswords((current) => !current)} aria-pressed={showPasswords}>{showPasswords ? "숨기기" : "보기"}</button>
          </div>
          <label htmlFor="new-password-confirmation">새 비밀번호 확인</label>
          <div className={`my-password-field ${passwordConfirmation && !passwordsMatch ? "invalid" : ""}`}>
            <input id="new-password-confirmation" type={showPasswords ? "text" : "password"} value={passwordConfirmation} onChange={(event) => setPasswordConfirmation(event.target.value)} minLength={8} maxLength={128} autoComplete="new-password" placeholder="새 비밀번호 다시 입력" aria-describedby="password-match-message" required />
          </div>
          <small id="password-match-message" className={passwordConfirmation && !passwordsMatch ? "password-mismatch" : "password-help"}>
            {passwordConfirmation && !passwordsMatch ? "비밀번호가 일치하지 않습니다." : "8자 이상 입력해 주세요."}
          </small>
          {notice && <div className={`notice ${notice.type}`} role={notice.type === "error" ? "alert" : "status"}>{notice.message}</div>}
          <button className="submit-button" type="submit" disabled={submitting || !newPassword || !passwordConfirmation || !passwordsMatch} aria-busy={submitting}>
            <span>{submitting ? "변경 중..." : "비밀번호 변경"}</span><b aria-hidden="true">→</b>
          </button>
        </form>
      </section>
      </article>

    </div>
    </div>

    {deleteOpen && <div className="account-delete-modal-layer">
      <button className="company-edit-backdrop" type="button" onClick={closeDeleteModal} aria-label="회원 탈퇴 창 닫기" />
      <form className="account-delete-modal" onSubmit={submitAccountDeletion} role="dialog" aria-modal="true" aria-labelledby="account-delete-title">
        <div className="account-delete-modal-head">
          <div><span className="section-label">FINAL CONFIRMATION</span><h2 id="account-delete-title">정말 탈퇴하시겠습니까?</h2></div>
          <button className="company-edit-close" type="button" onClick={closeDeleteModal} aria-label="닫기">×</button>
        </div>
        <p className="account-delete-warning"><strong>{session.user.email}</strong> 계정과 등록 기업, 수집·분석 기록이 모두 삭제됩니다. 진행 중인 수집도 중단되며 이 작업은 되돌릴 수 없습니다.</p>
        <label className="field-label" htmlFor="account-delete-password">확인을 위해 현재 비밀번호를 입력해 주세요.</label>
        <input id="account-delete-password" type="password" minLength={8} maxLength={128} value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} autoComplete="current-password" required autoFocus />
        {deleteError && <div className="notice error" role="alert">{deleteError}</div>}
        <div className="account-delete-modal-actions">
          <button className="secondary-button" type="button" onClick={closeDeleteModal} disabled={deleteSubmitting}>취소</button>
          <button className="account-delete-confirm" type="submit" disabled={deleteSubmitting || deletePassword.length < 8} aria-busy={deleteSubmitting}>{deleteSubmitting ? "탈퇴 처리 중..." : "계정 영구 삭제"}</button>
        </div>
      </form>
    </div>}
  </section>;
}
