import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api, getErrorMessage } from "../../api";

export default function SignupPage({ onAuthenticated }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => { document.title = "RISOTO · 회원가입"; }, []);

  const submit = async (event) => {
    event.preventDefault();
    if (password !== passwordConfirmation) {
      setError("비밀번호 확인이 일치하지 않습니다.");
      return;
    }
    setSubmitting(true); setError(null);
    try {
      const response = await api.post("/auth/signup", { email: email.trim().toLowerCase(), password });
      await onAuthenticated(response.data);
    } catch (requestError) {
      setError(getErrorMessage(requestError));
      setSubmitting(false);
    }
  };

  return <main className="login-page signup-page">
    <section className="login-story" aria-labelledby="signup-story-title">
      <div className="login-brand"><img src="/risoto-app-icon.png" alt="" aria-hidden="true" /><span>RISOTO</span><small>RISk Out Through Observation</small></div>
      <div className="login-story-copy">
        <span className="login-kicker">START YOUR WORKSPACE</span>
        <h1 id="signup-story-title">기업을 등록하고,<br /><em>위험 관찰을 시작하세요.</em></h1>
        <p>가입 직후 메인 기업을 설정하면 전용 워크스페이스에서 기업별 수집과 대응전략을 관리할 수 있습니다.</p>
      </div>
      <div className="login-pipeline" aria-label="시작 단계">
        <article><span>01</span><div><strong>계정 생성</strong><small>안전한 전용 세션</small></div></article>
        <article><span>02</span><div><strong>메인 기업</strong><small>첫 모니터링 대상 등록</small></div></article>
        <article><span>03</span><div><strong>위험 관찰</strong><small>실시간 신호 확인</small></div></article>
      </div>
      <div className="login-live-note"><i aria-hidden="true" /><span>워크스페이스별 데이터 분리</span></div>
    </section>
    <section className="login-access" aria-labelledby="signup-title">
      <div className="login-card">
        <div className="login-card-head"><span className="login-kicker">CREATE ACCOUNT</span><span className="login-role">무료 시작</span></div>
        <h2 id="signup-title">회원가입</h2>
        <p>사용할 이메일과 8자 이상의 비밀번호를 입력하세요.</p>
        <form className="login-form" onSubmit={submit}>
          <label htmlFor="signup-email">이메일</label>
          <input id="signup-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@company.com" autoComplete="username" required autoFocus />
          <label htmlFor="signup-password">비밀번호</label>
          <input id="signup-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="8자 이상 입력" autoComplete="new-password" minLength={8} required />
          <label htmlFor="signup-password-confirmation">비밀번호 확인</label>
          <input id="signup-password-confirmation" type="password" value={passwordConfirmation} onChange={(event) => setPasswordConfirmation(event.target.value)} placeholder="비밀번호를 다시 입력" autoComplete="new-password" minLength={8} required />
          {error && <div className="login-error" role="alert">{error}</div>}
          <button className="login-submit" type="submit" disabled={submitting}><span>{submitting ? "가입 중..." : "가입하고 메인 기업 등록"}</span></button>
        </form>
        <p className="auth-switch">이미 계정이 있나요? <Link to="/login">로그인</Link></p>
      </div>
      <p className="login-copyright">© 2026 RISOTO · Enterprise risk monitoring</p>
    </section>
  </main>;
}
