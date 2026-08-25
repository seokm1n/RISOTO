import { useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";

import WorkspaceApp from "./app/WorkspaceApp";
import LoginPage from "./features/auth/LoginPage";

const SESSION_KEY = "risoto-demo-session";

const readSession = () => {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(SESSION_KEY));
    if (stored?.email && ["user", "admin"].includes(stored.role)) return stored;
  } catch {
    window.sessionStorage.removeItem(SESSION_KEY);
  }
  return null;
};

// 인증 상태만 소유하고 로그인 화면과 제품 작업공간을 전환한다.
export default function App() {
  const [session, setSession] = useState(readSession);
  const location = useLocation();
  const navigate = useNavigate();
  const requestedPath = location.state?.from?.pathname;
  const requestedLocation = requestedPath && requestedPath !== "/login"
    ? `${requestedPath}${location.state?.from?.search ?? ""}${location.state?.from?.hash ?? ""}`
    : "/main";

  const login = (nextSession) => {
    window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(nextSession));
    setSession(nextSession);
  };

  const logout = () => {
    window.sessionStorage.removeItem(SESSION_KEY);
    setSession(null);
    navigate("/login", { replace: true });
  };

  if (!session) {
    return <Routes>
      <Route path="/login" element={<LoginPage onLogin={login} />} />
      <Route path="*" element={<Navigate to="/login" replace state={{ from: location }} />} />
    </Routes>;
  }

  return <Routes>
    <Route path="/login" element={<Navigate to={requestedLocation} replace />} />
    <Route path="*" element={<WorkspaceApp session={session} onLogout={logout} />} />
  </Routes>;
}
