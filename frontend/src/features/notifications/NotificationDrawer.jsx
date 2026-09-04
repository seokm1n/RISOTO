import { useEffect } from "react";

import { formatDate } from "../../shared/presentation";

const EMPTY_READ_IDS = new Set();
const NOOP = () => {};

function NotificationDrawer({ open, onClose, notifications, error, readIds = EMPTY_READ_IDS, markingAllRead = false, onMarkAllRead = NOOP, onRiskOpen = NOOP }) {
  const allowedItems = (notifications.items ?? []).filter((item) => item.type === "risk");

  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;
  const riskCount = allowedItems.filter((item) => item.type === "risk").length;
  const unreadCount = allowedItems.filter((item) => !readIds.has(item.id)).length;

  return <div className="notification-layer">
    <button className="notification-backdrop" type="button" aria-label="알림 패널 닫기" onClick={onClose} />
    <aside className="notification-drawer" id="notification-drawer" role="dialog" aria-modal="true" aria-labelledby="notification-drawer-title">
      <div className="notification-drawer-head"><div><span className="eyebrow">NOTIFICATION CENTER</span><h2 id="notification-drawer-title">위험 알림</h2><p>확인이 필요한 기업 위험 신호입니다.</p></div><button className="notification-close" type="button" onClick={onClose} aria-label="알림 닫기">×</button></div>
      <div className="notification-drawer-tabs" aria-label="알림 유형"><span className="active">위험 <strong>{riskCount}</strong></span></div>
      <div className="notification-drawer-tools"><span aria-live="polite">읽지 않음 {unreadCount}</span><button type="button" onClick={onMarkAllRead} disabled={unreadCount === 0 || markingAllRead}>{markingAllRead ? "처리 중..." : unreadCount === 0 ? "모두 읽음 완료" : "모두 읽음"}</button></div>
      {error && <div className="notification-load-error" role="status">알림을 갱신하지 못했습니다. 마지막 결과를 표시합니다.</div>}
      <div className="notification-drawer-list">{allowedItems.length ? allowedItems.map((item) => {
        const isRead = readIds.has(item.id);
        return <button className={`notification-drawer-item risk ${isRead ? "read" : "unread"}`} type="button" onClick={() => onRiskOpen(item)} key={item.id}><span className="notification-type-mark" aria-hidden="true" /><div><span>{isRead ? "읽음" : "새 위험 알림"}</span><strong>{item.title}</strong><strong className="notification-article-title risk-event-display-title">{item.message}</strong><small>{formatDate(item.created_at)}</small></div><b aria-hidden="true">→</b></button>;
      }) : <p className="notification-drawer-empty">현재 표시할 알림이 없습니다.</p>}</div>
    </aside>
  </div>;
}

export default NotificationDrawer;
