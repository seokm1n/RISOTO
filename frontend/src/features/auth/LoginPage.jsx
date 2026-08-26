import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api, getErrorMessage } from "../../api";

function LoginPage({ onAuthenticated }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => { document.title = "RISOTO · 로그인"; }, []);

  const submit = async (event) => {
    event.preventDefault();
    setSubmitting(true); setError(null);
    try {
      const response = await api.post("/auth/login", { email: email.trim().toLowerCase(), password });
      await onAuthenticated(response.data);
    } catch (requestError) {
      setError(getErrorMessage(requestError));
      setSubmitting(false);
    }
  };

  return <main className="login-page">
    <section className="login-story" aria-labelledby="login-story-title">
      <div className="login-brand"><img src="/risoto-app-icon.png" alt="" aria-hidden="true" /><span>RISOTO</span><small>RISk Out Through Observation</small></div>
      <div className="login-story-copy">
        <h1 id="login-story-title">위험 신호를 먼저 발견하고,<br /><em>대응은 더 빠르게.</em></h1>
        <p>실시간 기업 데이터를 정제하고 위험을 판별해, 근거 기반 대응전략까지 하나의 흐름으로 관리합니다.</p>
      </div>
    </section>

    <section className="login-access" aria-labelledby="login-title">
      <div className="login-card">
        <div className="login-card-head"><span className="login-kicker">MEMBER ACCESS</span></div>
        <h2 id="login-title">로그인</h2>
        <p>등록한 기업의 위험 현황과 대응전략을 확인하세요.</p>
        <form className="login-form" onSubmit={submit}>
          <label htmlFor="login-email">이메일</label>
          <input id="login-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@company.com" autoComplete="username" required autoFocus />
          <label htmlFor="login-password">비밀번호</label>
          <div className="login-password-field">
            <input id="login-password" type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="8자 이상 입력" autoComplete="current-password" minLength={8} required />
            <button type="button" onClick={() => setShowPassword((current) => !current)} aria-label={showPassword ? "비밀번호 숨기기" : "비밀번호 보기"} aria-pressed={showPassword}>{showPassword ? "숨기기" : "보기"}</button>
          </div>
          {error && <div className="login-error" role="alert">{error}</div>}
          <button className="login-submit" type="submit" disabled={submitting}><span>{submitting ? "로그인 중..." : "로그인"}</span></button>
        </form>
        <p className="auth-switch">처음 이용하시나요? <Link to="/signup">회원가입</Link></p>
      </div>
      <p className="login-copyright">© 2026 RISOTO · Enterprise risk monitoring</p>
    </section>
  </main>;
}

export default LoginPage;
