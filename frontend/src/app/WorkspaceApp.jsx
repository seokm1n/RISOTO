import { useCallback, useEffect, useState } from "react";
import {
  Navigate,
  Route,
  Routes,
  useBlocker,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";

import { api, getErrorMessage } from "../api";
import CollectionPage from "../features/collection/CollectionPage";
import CompanyAdministrationPage from "../features/companies/CompanyPages";
import MainPage from "../features/home/MainPage";
import ModelManagementPage from "../features/models/ModelManagementPage";
import NotificationDrawer from "../features/notifications/NotificationDrawer";
import RealtimePage from "../features/realtime/RealtimePage";
import { EMPTY_NOTIFICATIONS } from "../shared/presentation";

const NAV_ITEMS = [
  { id: "home", label: "메인", path: "/main", roles: ["user", "admin"] },
  { id: "companies", label: "기업 관리", path: "/companies", roles: ["user", "admin"] },
  { id: "collection", label: "수집", path: "/collection", roles: ["user", "admin"] },
  { id: "detail", label: "기업 상세", path: "/companies/overview", roles: ["user", "admin"] },
  { id: "models", label: "모델 관리", path: "/models", roles: ["admin"] },
];

const PAGE_TITLES = {
  home: "메인",
  collection: "수집",
  detail: "기업 상세",
  companies: "기업 관리",
  models: "모델 관리",
};

const numericParam = (value) => /^\d+$/.test(value ?? "") ? value : null;

const pageFromPath = (pathname) => {
  if (pathname === "/main") return "home";
  if (pathname === "/collection") return "collection";
  if (pathname === "/models") return "models";
  if (pathname === "/companies" || pathname === "/companies/new" || /^\/companies\/[^/]+\/settings$/.test(pathname)) return "companies";
  if (pathname === "/companies/overview" || /^\/companies\/[^/]+$/.test(pathname)) return "detail";
  return "home";
};

function CompanyDetailRoute({ canAdminister, onCompanyChange }) {
  const { companyId } = useParams();
  const [searchParams] = useSearchParams();
  const normalizedCompanyId = companyId ? numericParam(companyId) : null;
  const riskEventId = numericParam(searchParams.get("riskEventId"));

  if (companyId && !normalizedCompanyId) return <Navigate to="/companies/overview" replace />;
  return <RealtimePage
    key={normalizedCompanyId ?? "overview"}
    initialCompanyId={normalizedCompanyId}
    initialRiskEventId={riskEventId ? Number(riskEventId) : null}
    canAdminister={canAdminister}
    onCompanyChange={onCompanyChange}
  />;
}

function CompanySettingsRoute(props) {
  const { companyId } = useParams();
  const normalizedCompanyId = numericParam(companyId);
  if (!normalizedCompanyId) return <Navigate to="/companies" replace />;
  return <CompanyAdministrationPage {...props} mode="edit" initialCompanyId={normalizedCompanyId} />;
}

// 역할에 따라 접근 가능한 URL과 관리 기능을 제한하는 최상위 앱 셸이다.
export default function WorkspaceApp({ session, onLogout }) {
  const isAdmin = session.role === "admin";
  const location = useLocation();
  const navigate = useNavigate();
  const page = pageFromPath(location.pathname);
  const [managementDirty, setManagementDirty] = useState(false);
  const [notifications, setNotifications] = useState(EMPTY_NOTIFICATIONS);
  const [notificationError, setNotificationError] = useState(null);
  const [notificationOpen, setNotificationOpen] = useState(false);

  const shouldBlockNavigation = useCallback(({ currentLocation, nextLocation }) => (
    managementDirty
    && page === "companies"
    && currentLocation.pathname !== nextLocation.pathname
  ), [managementDirty, page]);
  const blocker = useBlocker(shouldBlockNavigation);

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm("저장하지 않은 변경사항을 버리고 이동할까요?")) blocker.proceed();
    else blocker.reset();
  }, [blocker]);

  const loadNotifications = useCallback(async () => {
    try {
      const response = await api.get("/notifications");
      setNotifications({ ...EMPTY_NOTIFICATIONS, ...response.data, items: response.data?.items ?? [] });
      setNotificationError(null);
    } catch (requestError) { setNotificationError(getErrorMessage(requestError)); }
  }, []);
  useEffect(() => {
    loadNotifications(); const timer = window.setInterval(loadNotifications, 30000);
    return () => window.clearInterval(timer);
  }, [loadNotifications]);
  useEffect(() => { document.title = `RISOTO · ${PAGE_TITLES[page]}`; }, [page]);

  const goTo = useCallback((path, options) => {
    setNotificationOpen(false);
    if (`${location.pathname}${location.search}` === path) return;
    navigate(path, options);
  }, [location.pathname, location.search, navigate]);

  const openCompanyDetail = useCallback((companyId, riskEventId = null, options) => {
    if (!companyId) { goTo("/companies/overview", options); return; }
    const riskQuery = riskEventId ? `?riskEventId=${encodeURIComponent(riskEventId)}` : "";
    goTo(`/companies/${encodeURIComponent(companyId)}${riskQuery}`, options);
  }, [goTo]);

  const openManagementCompany = useCallback((companyId, mode = companyId ? "edit" : "register") => {
    if (mode === "register") { goTo("/companies/new"); return; }
    goTo(companyId ? `/companies/${encodeURIComponent(companyId)}/settings` : "/companies");
  }, [goTo]);

  const changeCompanyAdminMode = useCallback((mode) => {
    goTo(mode === "register" ? "/companies/new" : "/companies");
  }, [goTo]);

  const changeDetailCompany = useCallback((companyId, options) => {
    openCompanyDetail(companyId, null, options);
  }, [openCompanyDetail]);

  const logout = () => {
    if (managementDirty && page === "companies" && !window.confirm("저장하지 않은 변경사항을 버리고 로그아웃할까요?")) return;
    onLogout();
  };

  const companyAdministrationProps = {
    onDirtyChange: setManagementDirty,
    onOpenCompany: openCompanyDetail,
    onEditCompany: (companyId) => openManagementCompany(companyId, "edit"),
    onModeChange: changeCompanyAdminMode,
  };
  const allowedNotificationItems = (notifications.items ?? []).filter((item) => isAdmin || item.type === "risk");
  const notificationTotal = allowedNotificationItems.length;

  return <main className="min-h-screen">
    <header className="topbar">
      <button className="brand" onClick={() => goTo("/main")}><img className="brand-icon" src="/risoto-app-icon.png" alt="" aria-hidden="true" />RISOTO<span>RISk Out Through Observation</span></button>
      <nav className="main-nav" aria-label="주요 화면">{NAV_ITEMS.filter((item) => item.roles.includes(session.role)).map((item) => <button className={page === item.id ? "active" : ""} aria-current={page === item.id ? "page" : undefined} onClick={() => goTo(item.path)} key={item.id}>{item.label}</button>)}</nav>
      <div className="topbar-actions">
        <button className="notification-bell" type="button" onClick={() => { loadNotifications(); setNotificationOpen(true); }} aria-label={`알림 ${notificationTotal}건`} aria-expanded={notificationOpen} aria-controls="notification-drawer" title="알림 보기"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z" /><path d="M10 21h4" /></svg>{notificationTotal > 0 && <span className="notification-badge" aria-hidden="true">{notificationTotal > 99 ? "99+" : notificationTotal}</span>}</button>
        <button className="account-button" type="button" onClick={logout} title={`${session.email} · 로그아웃`}><span>{session.email}</span><strong>{isAdmin ? "관리자" : "일반 사용자"} · 로그아웃</strong></button>
      </div>
    </header>

    <Routes>
      <Route path="/" element={<Navigate to="/main" replace />} />
      <Route path="/main" element={<MainPage canManageCompanies onOpenCompany={openCompanyDetail} onManageCompanies={openManagementCompany} />} />
      <Route path="/collection" element={<CollectionPage isAdmin onOpenCompany={openCompanyDetail} />} />
      <Route path="/companies" element={<CompanyAdministrationPage {...companyAdministrationProps} mode="edit" initialCompanyId={null} />} />
      <Route path="/companies/new" element={<CompanyAdministrationPage {...companyAdministrationProps} mode="register" initialCompanyId={null} />} />
      <Route path="/companies/:companyId/settings" element={<CompanySettingsRoute {...companyAdministrationProps} />} />
      <Route path="/companies/overview" element={<CompanyDetailRoute canAdminister onCompanyChange={changeDetailCompany} />} />
      <Route path="/companies/:companyId" element={<CompanyDetailRoute canAdminister onCompanyChange={changeDetailCompany} />} />
      <Route path="/models" element={isAdmin ? <ModelManagementPage /> : <Navigate to="/main" replace />} />
      <Route path="*" element={<Navigate to="/main" replace />} />
    </Routes>

    <NotificationDrawer open={notificationOpen} onClose={() => setNotificationOpen(false)} notifications={notifications} error={notificationError} role={session.role} onRiskOpen={(item) => item.company_id && openCompanyDetail(item.company_id, item.risk_event_id)} onModelOpen={() => goTo("/models")} />
  </main>;
}
