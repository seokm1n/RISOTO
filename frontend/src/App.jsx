import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";

import { api, setCsrfToken } from "./api";
import WorkspaceApp from "./features/app/WorkspaceApp";
import LoginPage from "./features/auth/LoginPage";
import SignupPage from "./features/auth/SignupPage";
import { MainCompanyOnboardingPage } from "./features/companies/CompanyPages";

function AuthLoading() {
  return <main className="auth-loading" aria-live="polite"><span className="monitor-loader" aria-hidden="true" /><p>로그인 상태를 확인하고 있습니다.</p></main>;
}

// 서버 세션을 단일 진실 공급원으로 삼아 공개·온보딩·제품 경로를 보호한다.
export default function App() {
  const [auth, setAuth] = useState(undefined);
  const location = useLocation();
  const navigate = useNavigate();

  const applyAuth = useCallback((payload) => {
    setCsrfToken(payload?.csrf_token);
    setAuth(payload ?? null);
    return payload;
  }, []);

  const refreshAuth = useCallback(async () => {
    try {
      const response = await api.get("/auth/me");
      return applyAuth(response.data);
    } catch (error) {
      if (error.response?.status !== 401) console.error("인증 상태 확인 실패", error);
      applyAuth(null);
      return null;
    }
  }, [applyAuth]);

  useEffect(() => { refreshAuth(); }, [refreshAuth]);
  useEffect(() => {
    const clearExpiredSession = () => applyAuth(null);
    window.addEventListener("risoto:unauthorized", clearExpiredSession);
    return () => window.removeEventListener("risoto:unauthorized", clearExpiredSession);
  }, [applyAuth]);

  const completeAuthentication = useCallback(async (payload) => {
    if (payload?.user) return applyAuth(payload);
    return refreshAuth();
  }, [applyAuth, refreshAuth]);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); }
    catch (error) { if (error.response?.status !== 401) console.error("로그아웃 요청 실패", error); }
    finally {
      applyAuth(null);
      navigate("/login", { replace: true });
    }
  }, [applyAuth, navigate]);

  if (auth === undefined) return <AuthLoading />;

  if (!auth) {
    return <Routes>
      <Route path="/login" element={<LoginPage onAuthenticated={completeAuthentication} />} />
      <Route path="/signup" element={<SignupPage onAuthenticated={completeAuthentication} />} />
      <Route path="*" element={<Navigate to="/login" replace state={{ from: location }} />} />
    </Routes>;
  }

  if (!auth.has_main_company) {
    return <Routes>
      <Route path="/onboarding/main-company" element={<MainCompanyOnboardingPage onCreated={refreshAuth} />} />
      <Route path="*" element={<Navigate to="/onboarding/main-company" replace />} />
    </Routes>;
  }

  const requestedPath = location.state?.from?.pathname;
  const requestedLocation = requestedPath && !["/login", "/signup", "/onboarding/main-company"].includes(requestedPath)
    ? `${requestedPath}${location.state?.from?.search ?? ""}${location.state?.from?.hash ?? ""}`
    : "/collection";

  return <Routes>
    <Route path="/login" element={<Navigate to={requestedLocation} replace />} />
    <Route path="/signup" element={<Navigate to="/collection" replace />} />
    <Route path="/onboarding/main-company" element={<Navigate to="/collection" replace />} />
    <Route path="*" element={<WorkspaceApp session={auth} onLogout={logout} />} />
  </Routes>;
}
