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
import AnalysisPipelinePage from "../analysis/AnalysisPipelinePage";
import CollectionPage from "../collection/CollectionPage";
import CompanyAdministrationPage from "../companies/CompanyPages";
import MainPage from "../home/MainPage";
import ModelManagementPage from "../models/ModelManagementPage";
import NotificationDrawer from "../notifications/NotificationDrawer";
import ArticleReviewPage from "../reviews/ArticleReviewPage";
import RiskManagementPage from "../risk-management/RiskManagementPage";
import { AppNoticeDialog, useAppConfirm } from "../../shared/components";
import { EMPTY_NOTIFICATIONS } from "../../shared/presentation";
import { useSharedResource } from "../../shared/useSharedResource";

const GENERAL_NAV_ITEMS = [
  { id: "main", label: "AI 리스크 브리핑", path: "/main" },
  { id: "statistics", label: "분석", path: "/analysis/collection" },
  { id: "risk-management", label: "대응", path: "/risk-management" },
  { id: "collection", label: "수집 관리", path: "/collection" },
  { id: "companies", label: "기업 관리", path: "/companies" },
];

const ADMIN_NAV_ITEMS = [
  { id: "admin-members", label: "회원 관리", path: "/admin/members" },
  { id: "admin-collection", label: "수집 관리", path: "/admin/collection" },
  { id: "admin-operations", label: "운영 관리", path: "/admin/operations" },
  { id: "admin-review", label: "기사 검수", path: "/admin/reviews" },
];

const PAGE_TITLES = {
  main: "AI 리스크 브리핑",
  collection: "수집 현황",
  statistics: "분석 통계",
  "risk-management": "위험 관리",
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
  if (pathname === "/risk-management") return "risk-management";
  if (pathname.startsWith("/analysis/")) return "statistics";
  if (pathname === "/companies" || pathname === "/companies/new" || /^\/companies\/[^/]+\/settings$/.test(pathname)) return "companies";
  if (pathname === "/companies/overview" || /^\/companies\/[^/]+$/.test(pathname)) return "statistics";
  return "collection";
};

function AnalysisStatisticsRoute() {
  const location = useLocation();
  const normalizedCompanyId = numericParam(location.state?.companyId);
  const params = new URLSearchParams(location.search);
  if (normalizedCompanyId) params.set("companyId", normalizedCompanyId);
  return <Navigate to={`/analysis/collection${params.size ? `?${params}` : ""}`} replace />;
}

function CollectionRoute({ onOpenCompany, onMonitoringChanged }) {
  const [searchParams] = useSearchParams();
  const articleCompanyId = numericParam(searchParams.get("articleCompanyId"));
  const articleDays = Number(numericParam(searchParams.get("days"))) || null;
  return <CollectionPage key={`${articleCompanyId ?? "collection"}-${articleDays ?? "all"}`} onOpenCompany={onOpenCompany} initialArticleCompanyId={articleCompanyId} initialArticleDays={articleDays} onMonitoringChanged={onMonitoringChanged} />;
}

function RiskManagementRoute() {
  return <RiskManagementPage canReview />;
}

function LegacyAnalysisStatisticsRedirect() {
  const { companyId } = useParams();
  const location = useLocation();
  const normalizedCompanyId = numericParam(companyId);
  const params = new URLSearchParams(location.search);
  if (normalizedCompanyId) params.set("companyId", normalizedCompanyId);
  return <Navigate
    to={`/analysis/collection${params.size ? `?${params}` : ""}`}
    replace
  />;
}

// 인증된 사용자에게 공통 관리 기능과 URL 기반 화면을 제공한다.
export default function WorkspaceApp({ session, onLogout, onAccountDeleted }) {
  const location = useLocation();
  const navigate = useNavigate();
  const isAdmin = session.user.role === "admin";
  const navItems = isAdmin ? ADMIN_NAV_ITEMS : GENERAL_NAV_ITEMS;
  const homePath = isAdmin ? "/admin/members" : "/main";
  const page = pageFromPath(location.pathname);
  const { data: userCompanies = [], refresh: refreshUserCompanies } = useSharedResource(
    isAdmin ? "skip:topbar-main-company" : "/companies",
    isAdmin
      ? () => Promise.resolve([])
      : () => api.get("/companies").then((response) => response.data),
    { intervalMs: isAdmin ? 0 : 30000 },
  );
  const mainCompany = userCompanies.find((company) => company.company_role === "main");
  const mainCollectionRunning = ["backfilling", "warming", "active"].includes(mainCompany?.monitoring_status);
  const [managementDirty, setManagementDirty] = useState(false);
  const [notifications, setNotifications] = useState(EMPTY_NOTIFICATIONS);
  const [notificationError, setNotificationError] = useState(null);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [readNotificationIds, setReadNotificationIds] = useState(() => new Set());
  const [logoutNoticeOpen, setLogoutNoticeOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const { confirm, confirmationDialog } = useAppConfirm();

  const shouldBlockNavigation = useCallback(({ currentLocation, nextLocation }) => (
    managementDirty
    && page === "companies"
    && currentLocation.pathname !== nextLocation.pathname
  ), [managementDirty, page]);
  const blocker = useBlocker(shouldBlockNavigation);

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    let active = true;
    confirm({
      kicker: "UNSAVED CHANGES",
      title: "저장하지 않은 변경사항을 버리고 이동할까요?",
      message: "이동하면 입력한 변경사항은 저장되지 않습니다.",
      confirmLabel: "이동",
      tone: "danger",
    }).then((confirmed) => {
      if (!active) return;
      if (confirmed) blocker.proceed();
      else blocker.reset();
    });
    return () => { active = false; };
  }, [blocker.state, confirm]);

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
    const params = new URLSearchParams();
    if (companyId) params.set("companyId", String(companyId));
    if (riskEventId) params.set("eventId", String(riskEventId));
    const query = params.size ? `?${params}` : "";
    goTo(`/analysis/${riskEventId ? "risk" : "collection"}${query}`, options);
  }, [goTo]);

  const requestLogout = async () => {
    if (managementDirty && page === "companies") {
      const confirmed = await confirm({
        kicker: "UNSAVED CHANGES",
        title: "저장하지 않은 변경사항을 버리고 로그아웃할까요?",
        message: "로그아웃하면 입력한 변경사항은 저장되지 않습니다.",
        confirmLabel: "계속",
        tone: "danger",
      });
      if (!confirmed) return;
    }
    if (isAdmin) {
      onLogout();
      return;
    }
    setLogoutNoticeOpen(true);
  };

  const confirmLogout = async () => {
    setLoggingOut(true);
    try {
      await onLogout();
    } finally {
      setLoggingOut(false);
    }
  };

  const companyAdministrationProps = {
    onDirtyChange: setManagementDirty,
    onOpenCompany: openAnalysisStatistics,
  };
  const allowedNotificationItems = (notifications.items ?? []).filter((item) => item.type === "risk");
  const notificationTotal = allowedNotificationItems.length;
  const markAllNotificationsRead = () => {
    setReadNotificationIds(new Set(allowedNotificationItems.map((item) => item.id)));
  };
  const openRiskNotification = (item) => {
    setReadNotificationIds((current) => {
      const next = new Set(current);
      next.add(item.id);
      return next;
    });
    if (item.company_id) openAnalysisStatistics(item.company_id, item.risk_event_id);
  };

  return <main className={`min-h-screen ${isAdmin ? "admin-app" : "general-app"}`}>
    <header className="topbar">
      <button className="brand" onClick={() => goTo(homePath)}><img className="brand-icon" src="/risoto-app-icon.png" alt="" aria-hidden="true" />RISOTO<span>RISk Out Through Observation</span></button>
      <nav className="main-nav" aria-label="주요 화면">{navItems.map((item) => <button className={page === item.id ? "active" : ""} aria-current={page === item.id ? "page" : undefined} onClick={() => goTo(item.path)} key={item.id}>{item.label}</button>)}</nav>
      <div className="topbar-actions">
        {!isAdmin && mainCompany && <span className={`topbar-live-collecting ${mainCollectionRunning ? "running" : "stopped"}`} role="status" aria-live="polite" aria-label={mainCollectionRunning ? "실시간 수집중" : "수집 중지"} title={mainCollectionRunning ? "실시간 수집중" : "수집 중지"}><i className="topbar-live-spinner" aria-hidden="true" /><span className="topbar-live-label">{mainCollectionRunning ? "실시간 수집중" : "수집 중지"}</span></span>}
        <button className="notification-siren" type="button" onClick={() => { loadNotifications(); setNotificationOpen(true); }} aria-label={`위험 알림 ${notificationTotal}건`} aria-expanded={notificationOpen} aria-controls="notification-drawer" title="위험 알림 보기"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 15h12l-1-6a5 5 0 0 0-10 0l-1 6Z" /><path d="M4 15h16v3H4z" /><path d="M8 21h8" /><path d="M12 3V1" /><path d="m5 5-1.5-1.5M19 5l1.5-1.5M2 11H0M22 11h2" /></svg>{notificationTotal > 0 && <span className="notification-badge" aria-hidden="true">{notificationTotal > 99 ? "99+" : notificationTotal}</span>}</button>
        <button className={`account-button ${page === "account" ? "active" : ""}`} type="button" onClick={() => goTo("/account")} title={`${session.user.email} · 마이페이지`} aria-current={page === "account" ? "page" : undefined}><span>{session.user.email}</span><strong>마이페이지</strong></button>
        <button className="logout-button" type="button" onClick={requestLogout}>로그아웃</button>
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
      <Route path="/account" element={<MyPage session={session} onAccountDeleted={onAccountDeleted} />} />
      <Route path="*" element={<Navigate to="/admin/members" replace />} />
    </> : <>
      <Route path="/" element={<Navigate to="/main" replace />} />
      <Route path="/main" element={<MainPage onOpenCompany={openAnalysisStatistics} />} />
      <Route path="/account" element={<MyPage session={session} onAccountDeleted={onAccountDeleted} />} />
      <Route path="/collection" element={<CollectionRoute onOpenCompany={openAnalysisStatistics} onMonitoringChanged={refreshUserCompanies} />} />
      <Route path="/companies" element={<CompanyAdministrationPage {...companyAdministrationProps} />} />
      <Route path="/companies/new" element={<Navigate to="/companies" replace />} />
      <Route path="/companies/:companyId/settings" element={<Navigate to="/companies" replace />} />
      <Route path="/companies/main" element={<AnalysisStatisticsRoute />} />
      <Route path="/analysis/response" element={<Navigate to="/risk-management" replace />} />
      <Route path="/analysis/:stage" element={<AnalysisPipelinePage />} />
      <Route path="/risk-management" element={<RiskManagementRoute />} />
      <Route path="/companies/overview" element={<Navigate to="/companies/main" replace />} />
      <Route path="/companies/:companyId" element={<LegacyAnalysisStatisticsRedirect />} />
      <Route path="/operations" element={<Navigate to="/main" replace />} />
      <Route path="/models" element={<Navigate to="/main" replace />} />
      <Route path="/reviews" element={<Navigate to="/main" replace />} />
      <Route path="*" element={<Navigate to="/main" replace />} />
    </>}</Routes>

    <NotificationDrawer open={notificationOpen} onClose={() => setNotificationOpen(false)} notifications={{ ...notifications, items: allowedNotificationItems }} error={notificationError} readIds={readNotificationIds} onMarkAllRead={markAllNotificationsRead} onRiskOpen={openRiskNotification} />
    {confirmationDialog}
    {logoutNoticeOpen && <AppNoticeDialog kicker="COLLECTION NOTICE" title="로그아웃 후에도 수집은 계속됩니다" confirmLabel="로그아웃" cancelLabel="취소" onClose={() => setLogoutNoticeOpen(false)} onConfirm={confirmLogout} busy={loggingOut}>
      <p>로그아웃하거나 브라우저를 닫아도 백엔드가 실행 중이면 등록한 기업의 실시간 수집은 계속됩니다.</p>
      <p className="app-notice-detail">수집을 멈추려면 로그아웃하기 전에 수집 현황에서 수집을 정지해 주세요.</p>
    </AppNoticeDialog>}
  </main>;
}
