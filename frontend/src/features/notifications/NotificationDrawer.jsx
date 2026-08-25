import { useEffect, useState } from "react";

import { formatDate } from "../../shared/presentation";

function NotificationDrawer({ open, onClose, notifications, error, role, onRiskOpen, onModelOpen }) {
  const [filter, setFilter] = useState("all");
  const [readIds, setReadIds] = useState(() => new Set());
  const isAdmin = role === "admin";
  const allowedItems = (notifications.items ?? []).filter((item) => isAdmin || item.type === "risk");
  const filteredItems = allowedItems.filter((item) => filter === "all" || (filter === "risk" ? item.type === "risk" : item.type !== "risk"));

  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, onClose]);
  useEffect(() => { if (!isAdmin && filter === "model") setFilter("all"); }, [filter, isAdmin]);

  if (!open) return null;
  const riskCount = allowedItems.filter((item) => item.type === "risk").length;
  const modelCount = allowedItems.length - riskCount;
  const openItem = (item) => {
    setReadIds((current) => new Set([...current, item.id]));
    if (item.type === "risk") onRiskOpen(item); else onModelOpen(item);
  };

  return <div className="notification-layer">
    <button className="notification-backdrop" type="button" aria-label="알림 패널 닫기" onClick={onClose} />
    <aside className="notification-drawer" id="notification-drawer" role="dialog" aria-modal="true" aria-labelledby="notification-drawer-title">
      <div className="notification-drawer-head"><div><span className="eyebrow">NOTIFICATION CENTER</span><h2 id="notification-drawer-title">알림</h2><p>확인이 필요한 위험과 모델 운영 소식입니다.</p></div><button className="notification-close" type="button" onClick={onClose} aria-label="알림 닫기">×</button></div>
      <div className="notification-drawer-tabs" role="tablist" aria-label="알림 유형"><button type="button" role="tab" aria-selected={filter === "all"} className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>전체 <strong>{allowedItems.length}</strong></button><button type="button" role="tab" aria-selected={filter === "risk"} className={filter === "risk" ? "active" : ""} onClick={() => setFilter("risk")}>위험 <strong>{riskCount}</strong></button>{isAdmin && <button type="button" role="tab" aria-selected={filter === "model"} className={filter === "model" ? "active" : ""} onClick={() => setFilter("model")}>모델 <strong>{modelCount}</strong></button>}</div>
      <div className="notification-drawer-tools"><span>읽지 않음 {allowedItems.filter((item) => !readIds.has(item.id)).length}</span><button type="button" onClick={() => setReadIds(new Set(allowedItems.map((item) => item.id)))}>모두 읽음</button></div>
      {error && <div className="notification-load-error" role="status">알림을 갱신하지 못했습니다. 마지막 결과를 표시합니다.</div>}
      <div className="notification-drawer-list">{filteredItems.length ? filteredItems.map((item) => { const isRisk = item.type === "risk"; const isPromotionReady = item.type === "model_promotion_ready"; return <button className={`notification-drawer-item ${isRisk ? "risk" : "model"} ${isPromotionReady ? "model-ready" : ""} ${readIds.has(item.id) ? "read" : "unread"}`} type="button" onClick={() => openItem(item)} key={item.id}><span className="notification-type-mark" aria-hidden="true" /><div><span>{isRisk ? "위험 알림" : "모델 알림"}</span><strong>{item.title}</strong><p>{item.message}</p><small>{formatDate(item.created_at)}</small></div><b aria-hidden="true">→</b></button>; }) : <p className="notification-drawer-empty">현재 표시할 알림이 없습니다.</p>}</div>
    </aside>
  </div>;
}

export default NotificationDrawer;
