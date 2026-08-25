import { useEffect, useState } from "react";

function LoginPage({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => { document.title = "RISOTO · 로그인"; }, []);

  const submit = (event) => {
    event.preventDefault();
    setSubmitting(true);
    window.setTimeout(() => onLogin({ email: email.trim(), role }), 450);
  };

  return <main className="login-page">
    <section className="login-story" aria-labelledby="login-story-title">
      <div className="login-brand"><img src="/risoto-app-icon.png" alt="" aria-hidden="true" /><span>RISOTO</span><small>RISk Out Through Observation</small></div>
      <div className="login-story-copy">
        <span className="login-kicker">ENTERPRISE RISK INTELLIGENCE</span>
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

    <section className="login-access" aria-labelledby="login-title">
      <div className="login-card">
        <div className="login-role-switch" role="tablist" aria-label="로그인 유형"><button type="button" role="tab" aria-selected={role === "user"} className={role === "user" ? "active" : ""} onClick={() => setRole("user")}>일반 사용자 로그인</button><button type="button" role="tab" aria-selected={role === "admin"} className={role === "admin" ? "active" : ""} onClick={() => setRole("admin")}>관리자 로그인</button></div>
        <div className="login-card-head"><span className="login-kicker">{role === "admin" ? "ADMIN CONSOLE" : "USER ACCESS"}</span><span className={`login-role ${role}`}>{role === "admin" ? "관리자" : "일반 사용자"}</span></div>
        <h2 id="login-title">{role === "admin" ? "관리자 로그인" : "일반 사용자 로그인"}</h2>
        <p>{role === "admin" ? "기업·수집·모델을 관리할 관리자 계정으로 접속하세요." : "기업 위험 현황과 대응전략을 확인할 계정으로 접속하세요."}</p>
        <form className="login-form" onSubmit={submit}>
          <label htmlFor="login-email">이메일</label>
          <input id="login-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder={role === "admin" ? "admin@company.com" : "user@company.com"} autoComplete="username" required autoFocus />
          <label htmlFor="login-password">비밀번호</label>
          <div className="login-password-field">
            <input id="login-password" type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="8자 이상 입력" autoComplete="current-password" minLength={8} required />
            <button type="button" onClick={() => setShowPassword((current) => !current)} aria-label={showPassword ? "비밀번호 숨기기" : "비밀번호 보기"} aria-pressed={showPassword}>{showPassword ? "숨기기" : "보기"}</button>
          </div>
          <button className="login-submit" type="submit" disabled={submitting}><span>{submitting ? "로그인 중..." : role === "admin" ? "관리자 로그인" : "일반 사용자 로그인"}</span></button>
        </form>
      </div>
      <p className="login-copyright">© 2026 RISOTO · Enterprise risk monitoring</p>
    </section>
  </main>;
}

export default LoginPage;
