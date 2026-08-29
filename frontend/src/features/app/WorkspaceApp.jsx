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

import { api, getErrorMessage } from "../../api";
import AdminDashboardPage from "../admin/AdminDashboardPage";
import MyPage from "../account/MyPage";
import AnalysisStatisticsPage from "../analysis/AnalysisStatisticsPage";
import CollectionPage from "../collection/CollectionPage";
import CompanyAdministrationPage from "../companies/CompanyPages";
import MainPage from "../home/MainPage";
import ModelManagementPage from "../models/ModelManagementPage";
import NotificationDrawer from "../notifications/NotificationDrawer";
import ArticleReviewPage from "../reviews/ArticleReviewPage";
import { EMPTY_NOTIFICATIONS } from "../../shared/presentation";

const GENERAL_NAV_ITEMS = [
  { id: "main", label: "요약", path: "/main" },
  { id: "collection", label: "수집 현황", path: "/collection" },
  { id: "statistics", label: "분석 통계", path: "/companies/overview" },
  { id: "companies", label: "기업 목록", path: "/companies" },
];

const ADMIN_NAV_ITEMS = [
  { id: "admin-members", label: "회원 관리", path: "/admin/members" },
  { id: "admin-collection", label: "수집 관리", path: "/admin/collection" },
  { id: "admin-operations", label: "운영 관리", path: "/admin/operations" },
  { id: "admin-review", label: "기사 검수", path: "/admin/reviews" },
];

const PAGE_TITLES = {
  main: "요약",
  collection: "수집 현황",
  statistics: "분석 통계",
  companies: "기업 목록",
  account: "마이페이지",
  "admin-members": "회원 관리",
  "admin-collection": "수집 관리",
  "admin-operations": "운영 관리",
  "admin-review": "기사 검수",
};

const numericParam = (value) => /^\d+$/.test(value ?? "") ? value : null;

const pageFromPath = (pathname) => {
  if (pathname === "/main") return "main";
  if (pathname === "/account") return "account";
  if (pathname === "/admin/members") return "admin-members";
  if (pathname === "/admin/collection") return "admin-collection";
  if (pathname === "/admin/operations") return "admin-operations";
  if (pathname === "/admin/reviews") return "admin-review";
  if (pathname === "/operations" || pathname === "/models") return "admin-operations";
  if (pathname === "/reviews") return "admin-review";
  if (pathname === "/collection") return "collection";
  if (pathname === "/companies" || pathname === "/companies/new" || /^\/companies\/[^/]+\/settings$/.test(pathname)) return "companies";
  if (pathname === "/companies/overview" || /^\/companies\/[^/]+$/.test(pathname)) return "statistics";
  return "collection";
};

function AnalysisStatisticsRoute({ canAdminister, onCompanyChange }) {
  const { companyId } = useParams();
  const [searchParams] = useSearchParams();
  const normalizedCompanyId = companyId ? numericParam(companyId) : null;
  const riskEventId = numericParam(searchParams.get("riskEventId"));

  if (companyId && !normalizedCompanyId) return <Navigate to="/companies/overview" replace />;
  return <AnalysisStatisticsPage
    key={normalizedCompanyId ?? "overview"}
    initialCompanyId={normalizedCompanyId}
    initialRiskEventId={riskEventId ? Number(riskEventId) : null}
    canAdminister={canAdminister}
    onCompanyChange={onCompanyChange}
  />;
}

function MainCompanyOverviewRedirect() {
  const [mainCompanyId, setMainCompanyId] = useState(undefined);
  useEffect(() => {
    let active = true;
    api.get("/companies").then((response) => {
      if (active) setMainCompanyId(response.data.find((company) => company.company_role === "main")?.id ?? null);
    }).catch(() => active && setMainCompanyId(null));
    return () => { active = false; };
  }, []);
  if (mainCompanyId === undefined) return <p className="empty-state">나의 기업을 불러오는 중입니다.</p>;
  return <Navigate to={mainCompanyId ? `/companies/${mainCompanyId}` : "/companies"} replace />;
}

// 인증된 사용자에게 공통 관리 기능과 URL 기반 화면을 제공한다.
export default function WorkspaceApp({ session, onLogout }) {
  const location = useLocation();
  const navigate = useNavigate();
  const isAdmin = session.user.role === "admin";
  const navItems = isAdmin ? ADMIN_NAV_ITEMS : GENERAL_NAV_ITEMS;
  const homePath = isAdmin ? "/admin/members" : "/main";
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

  const openAnalysisStatistics = useCallback((companyId, riskEventId = null, options) => {
    if (!companyId) { goTo("/companies/overview", options); return; }
    const riskQuery = riskEventId ? `?riskEventId=${encodeURIComponent(riskEventId)}` : "";
    goTo(`/companies/${encodeURIComponent(companyId)}${riskQuery}`, options);
  }, [goTo]);

  const changeStatisticsCompany = useCallback((companyId, options) => {
    openAnalysisStatistics(companyId, null, options);
  }, [openAnalysisStatistics]);

  const logout = () => {
    if (managementDirty && page === "companies" && !window.confirm("저장하지 않은 변경사항을 버리고 로그아웃할까요?")) return;
    onLogout();
  };

  const companyAdministrationProps = {
    onDirtyChange: setManagementDirty,
    onOpenCompany: openAnalysisStatistics,
  };
  const allowedNotificationItems = (notifications.items ?? []).filter((item) => item.type === "risk");
  const notificationTotal = allowedNotificationItems.length;

  return <main className={`min-h-screen ${isAdmin ? "admin-app" : "general-app"}`}>
    <header className="topbar">
      <button className="brand" onClick={() => goTo(homePath)}><img className="brand-icon" src="/risoto-app-icon.png" alt="" aria-hidden="true" />RISOTO<span>RISk Out Through Observation</span></button>
      <nav className="main-nav" aria-label="주요 화면">{navItems.map((item) => <button className={page === item.id ? "active" : ""} aria-current={page === item.id ? "page" : undefined} onClick={() => goTo(item.path)} key={item.id}>{item.label}</button>)}</nav>
      <div className="topbar-actions">
        <button className="notification-siren" type="button" onClick={() => { loadNotifications(); setNotificationOpen(true); }} aria-label={`위험 알림 ${notificationTotal}건`} aria-expanded={notificationOpen} aria-controls="notification-drawer" title="위험 알림 보기"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 15h12l-1-6a5 5 0 0 0-10 0l-1 6Z" /><path d="M4 15h16v3H4z" /><path d="M8 21h8" /><path d="M12 3V1" /><path d="m5 5-1.5-1.5M19 5l1.5-1.5M2 11H0M22 11h2" /></svg>{notificationTotal > 0 && <span className="notification-badge" aria-hidden="true">{notificationTotal > 99 ? "99+" : notificationTotal}</span>}</button>
        <button className={`account-button ${page === "account" ? "active" : ""}`} type="button" onClick={() => goTo("/account")} title={`${session.user.email} · 마이페이지`} aria-current={page === "account" ? "page" : undefined}><span>{session.user.email}</span><strong>마이페이지</strong></button>
        <button className="logout-button" type="button" onClick={logout}>로그아웃</button>
      </div>
    </header>

    <Routes>{isAdmin ? <>
      <Route path="/" element={<Navigate to="/admin/members" replace />} />
      <Route path="/admin/members" element={<AdminDashboardPage view="members" />} />
      <Route path="/admin/collection" element={<AdminDashboardPage view="collection" />} />
      <Route path="/admin/operations" element={<ModelManagementPage />} />
      <Route path="/admin/reviews" element={<ArticleReviewPage />} />
      <Route path="/operations" element={<Navigate to="/admin/operations" replace />} />
      <Route path="/models" element={<Navigate to="/admin/operations" replace />} />
      <Route path="/reviews" element={<Navigate to="/admin/reviews" replace />} />
      <Route path="/account" element={<MyPage session={session} />} />
      <Route path="*" element={<Navigate to="/admin/members" replace />} />
    </> : <>
      <Route path="/" element={<Navigate to="/main" replace />} />
      <Route path="/main" element={<MainPage onOpenCompany={openAnalysisStatistics} />} />
      <Route path="/account" element={<MyPage session={session} />} />
      <Route path="/collection" element={<CollectionPage onOpenCompany={openAnalysisStatistics} />} />
      <Route path="/companies" element={<CompanyAdministrationPage {...companyAdministrationProps} />} />
      <Route path="/companies/new" element={<Navigate to="/companies" replace />} />
      <Route path="/companies/:companyId/settings" element={<Navigate to="/companies" replace />} />
      <Route path="/companies/overview" element={<MainCompanyOverviewRedirect />} />
      <Route path="/companies/:companyId" element={<AnalysisStatisticsRoute canAdminister onCompanyChange={changeStatisticsCompany} />} />
      <Route path="/operations" element={<Navigate to="/main" replace />} />
      <Route path="/models" element={<Navigate to="/main" replace />} />
      <Route path="/reviews" element={<Navigate to="/main" replace />} />
      <Route path="*" element={<Navigate to="/main" replace />} />
    </>}</Routes>

    <NotificationDrawer open={notificationOpen} onClose={() => setNotificationOpen(false)} notifications={{ ...notifications, items: allowedNotificationItems }} error={notificationError} onRiskOpen={(item) => item.company_id && openAnalysisStatistics(item.company_id, item.risk_event_id)} />
  </main>;
}
