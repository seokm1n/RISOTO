import { useEffect, useState } from "react";

import { formatDate } from "../../shared/presentation";

function NotificationDrawer({ open, onClose, notifications, error, onRiskOpen }) {
  const [readIds, setReadIds] = useState(() => new Set());
  const allowedItems = (notifications.items ?? []).filter((item) => item.type === "risk");

  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;
  const riskCount = allowedItems.filter((item) => item.type === "risk").length;
  const openItem = (item) => {
    setReadIds((current) => new Set([...current, item.id]));
    onRiskOpen(item);
  };

  return <div className="notification-layer">
    <button className="notification-backdrop" type="button" aria-label="알림 패널 닫기" onClick={onClose} />
    <aside className="notification-drawer" id="notification-drawer" role="dialog" aria-modal="true" aria-labelledby="notification-drawer-title">
      <div className="notification-drawer-head"><div><span className="eyebrow">NOTIFICATION CENTER</span><h2 id="notification-drawer-title">위험 알림</h2><p>확인이 필요한 워크스페이스 위험 신호입니다.</p></div><button className="notification-close" type="button" onClick={onClose} aria-label="알림 닫기">×</button></div>
      <div className="notification-drawer-tabs" aria-label="알림 유형"><span className="active">위험 <strong>{riskCount}</strong></span></div>
      <div className="notification-drawer-tools"><span>읽지 않음 {allowedItems.filter((item) => !readIds.has(item.id)).length}</span><button type="button" onClick={() => setReadIds(new Set(allowedItems.map((item) => item.id)))}>모두 읽음</button></div>
      {error && <div className="notification-load-error" role="status">알림을 갱신하지 못했습니다. 마지막 결과를 표시합니다.</div>}
      <div className="notification-drawer-list">{allowedItems.length ? allowedItems.map((item) => <button className={`notification-drawer-item risk ${readIds.has(item.id) ? "read" : "unread"}`} type="button" onClick={() => openItem(item)} key={item.id}><span className="notification-type-mark" aria-hidden="true" /><div><span>위험 알림</span><strong>{item.title}</strong><p>{item.message}</p><small>{formatDate(item.created_at)}</small></div><b aria-hidden="true">→</b></button>) : <p className="notification-drawer-empty">현재 표시할 알림이 없습니다.</p>}</div>
    </aside>
  </div>;
}

export default NotificationDrawer;
