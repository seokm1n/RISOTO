import { useEffect, useState } from "react";

import { api, getErrorMessage } from "../../api";

const TITLES = { login: "로그인", signup: "회원가입" };

// 로그인과 회원가입을 별도 페이지로 나누지 않고, 하나의 랜딩 화면에서
// 버튼으로 여는 팝업으로 둘 다 처리한다.
function LoginPage({ onAuthenticated }) {
  const [modalKind, setModalKind] = useState(() => window.location.pathname === "/signup" ? "signup" : null);

  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [showLoginPassword, setShowLoginPassword] = useState(false);
  const [loginSubmitting, setLoginSubmitting] = useState(false);
  const [loginError, setLoginError] = useState(null);

  const [signupEmail, setSignupEmail] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [signupPasswordConfirmation, setSignupPasswordConfirmation] = useState("");
  const [signupSubmitting, setSignupSubmitting] = useState(false);
  const [signupError, setSignupError] = useState(null);

  useEffect(() => { document.title = `RISOTO · ${TITLES[modalKind] ?? "로그인"}`; }, [modalKind]);

  useEffect(() => {
    if (!modalKind) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") setModalKind(null); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [modalKind]);

  const submitLogin = async (event) => {
    event.preventDefault();
    setLoginSubmitting(true); setLoginError(null);
    try {
      const response = await api.post("/auth/login", { email: loginEmail.trim().toLowerCase(), password: loginPassword });
      await onAuthenticated(response.data);
    } catch (requestError) {
      setLoginError(getErrorMessage(requestError));
      setLoginSubmitting(false);
    }
  };

  const submitSignup = async (event) => {
    event.preventDefault();
    if (signupPassword !== signupPasswordConfirmation) {
      setSignupError("비밀번호 확인이 일치하지 않습니다.");
      return;
    }
    setSignupSubmitting(true); setSignupError(null);
    try {
      const response = await api.post("/auth/signup", { email: signupEmail.trim().toLowerCase(), password: signupPassword });
      await onAuthenticated(response.data);
    } catch (requestError) {
      setSignupError(getErrorMessage(requestError));
      setSignupSubmitting(false);
    }
  };

  return <main className="login-landing">
    <section className="login-story" aria-labelledby="login-story-title">
      <div className="login-topbar">
        <div className="login-brand"><img src="/risoto-app-icon.png" alt="" aria-hidden="true" /><span>RISOTO</span><small>RISk Out Through Observation</small></div>
        <div className="login-topbar-actions">
          <button className="login-trigger" type="button" onClick={() => setModalKind("login")}>로그인</button>
          <button className="login-trigger" type="button" onClick={() => setModalKind("signup")}>회원가입</button>
        </div>
      </div>
      <div className="login-story-copy">
        <span className="login-agent-badge"><i aria-hidden="true" />AI RISK ANALYSIS AGENT</span>
        <h1 id="login-story-title">위험 신호를 먼저 발견하고,<br /><em>대응은 더 빠르게.</em></h1>
        <p>실시간 기업 데이터를 정제하고 위험을 판별해, 근거 기반 대응전략까지 하나의 흐름으로 관리합니다.</p>
      </div>
      <div className="login-pipeline" aria-label="RISOTO 분석 흐름">
        <article><span>01</span><div><strong>실시간 수집</strong><small>기업별 이슈 신호 관찰</small></div></article>
        <article><span>02</span><div><strong>위험 판정</strong><small>이상 징후와 위험 유형 분석</small></div></article>
        <article><span>03</span><div><strong>대응 관리</strong><small>근거 기반 전략 검토·승인</small></div></article>
      </div>
      <div className="login-live-note"><i aria-hidden="true" /><span>15분 단위 리스크 신호 모니터링</span></div>
    </section>

    {modalKind === "login" && <div className="login-modal-layer">
      <button className="login-modal-backdrop" type="button" aria-label="로그인 창 닫기" onClick={() => setModalKind(null)} />
      <div className="login-modal" role="dialog" aria-modal="true" aria-labelledby="login-title">
        <button className="login-modal-close" type="button" onClick={() => setModalKind(null)} aria-label="닫기">×</button>
        <div className="login-card">
          <div className="login-card-head"><span className="login-kicker">MEMBER ACCESS</span><span className="login-role">사용자 계정</span></div>
          <h2 id="login-title">로그인</h2>
          <p>등록한 기업의 위험 현황과 대응전략을 확인하세요.</p>
          <form className="login-form" onSubmit={submitLogin}>
            <label htmlFor="login-email">이메일</label>
            <input id="login-email" type="email" value={loginEmail} onChange={(event) => setLoginEmail(event.target.value)} placeholder="name@company.com" autoComplete="username" required autoFocus />
            <label htmlFor="login-password">비밀번호</label>
            <div className="login-password-field">
              <input id="login-password" type={showLoginPassword ? "text" : "password"} value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} placeholder="8자 이상 입력" autoComplete="current-password" minLength={8} required />
              <button type="button" onClick={() => setShowLoginPassword((current) => !current)} aria-label={showLoginPassword ? "비밀번호 숨기기" : "비밀번호 보기"} aria-pressed={showLoginPassword}>{showLoginPassword ? "숨기기" : "보기"}</button>
            </div>
            {loginError && <div className="login-error" role="alert">{loginError}</div>}
            <button className="login-submit" type="submit" disabled={loginSubmitting}><span>{loginSubmitting ? "로그인 중..." : "로그인"}</span></button>
          </form>
          <p className="auth-switch">처음 이용하시나요? <button type="button" className="auth-switch-link" onClick={() => setModalKind("signup")}>회원가입</button></p>
        </div>
      </div>
    </div>}

    {modalKind === "signup" && <div className="login-modal-layer">
      <button className="login-modal-backdrop" type="button" aria-label="회원가입 창 닫기" onClick={() => setModalKind(null)} />
      <div className="login-modal" role="dialog" aria-modal="true" aria-labelledby="signup-title">
        <button className="login-modal-close" type="button" onClick={() => setModalKind(null)} aria-label="닫기">×</button>
        <div className="login-card">
          <div className="login-card-head"><span className="login-kicker">CREATE ACCOUNT</span></div>
          <h2 id="signup-title">회원가입</h2>
          <p>사용할 이메일과 8자 이상의 비밀번호를 입력하세요.</p>
          <form className="login-form" onSubmit={submitSignup}>
            <label htmlFor="signup-email">이메일</label>
            <input id="signup-email" type="email" value={signupEmail} onChange={(event) => setSignupEmail(event.target.value)} placeholder="name@company.com" autoComplete="username" required autoFocus />
            <label htmlFor="signup-password">비밀번호</label>
            <input id="signup-password" type="password" value={signupPassword} onChange={(event) => setSignupPassword(event.target.value)} placeholder="8자 이상 입력" autoComplete="new-password" minLength={8} required />
            <label htmlFor="signup-password-confirmation">비밀번호 확인</label>
            <input id="signup-password-confirmation" type="password" value={signupPasswordConfirmation} onChange={(event) => setSignupPasswordConfirmation(event.target.value)} placeholder="비밀번호를 다시 입력" autoComplete="new-password" minLength={8} required />
            {signupError && <div className="login-error" role="alert">{signupError}</div>}
            <button className="login-submit" type="submit" disabled={signupSubmitting}><span>{signupSubmitting ? "가입 중..." : "가입하고 메인 기업 등록"}</span></button>
          </form>
          <p className="auth-switch">이미 계정이 있나요? <button type="button" className="auth-switch-link" onClick={() => setModalKind("login")}>로그인</button></p>
        </div>
      </div>
    </div>}
  </main>;
}

export default LoginPage;
