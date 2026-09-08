import "./Icon.css";

const ICONS = {
  briefing: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><path d="M14 15h7m-7 5h7" /></>,
  analysis: <><path d="M4 3v17h17M8 15l4-5 4 2 5-7" /></>,
  collection: <><path d="M12 3v11m-4-4 4 4 4-4M4 14v6h16v-6" /></>,
  companies: <><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 7h1m4 0h1m-6 4h1m4 0h1m-6 4h1m4 0h1m-5 6v-3h4v3" /></>,
  members: <><circle cx="9" cy="7" r="3" /><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 4v3" /></>,
  articles: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 7h8M8 11h8M8 15h4" /></>,
  risk: <><path d="M12 3 4.5 6v5.5c0 4.4 3.1 7.4 7.5 9.5 4.4-2.1 7.5-5.1 7.5-9.5V6Z" /><path d="M12 8v5m0 3h.01" /></>,
  sentiment: <><path d="M20 14a3 3 0 0 1-3 3H9l-5 4V6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3Z" /><path d="M8 10h8" /></>,
  events: <><path d="M5 21V4m0 0c5-4 9 4 14 0v10c-5 4-9-4-14 0" /></>,
  filtering: <path d="M3 4h18l-7 8v7l-4 2v-9Z" />,
  response: <><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 3h6v4H9Zm-1 11 3 3 5-6" /></>,
  operations: <><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4M10 10h4v4h-4Z" /></>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M7 3v4m10-4v4M3 11h18m-13 4h2m4 0h2m-8 3h2" /></>,
  bell: <><path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5ZM10 21h4M12 2v2" /></>,
  edit: <><path d="M4 20h4l11-11-4-4L4 16v4Zm9.5-13.5 4 4" /></>,
  refresh: <><path d="M20 11a8 8 0 1 0 1.2 5.2M20 4v7h-7" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10h.01" /></>,
};

const accentIcons = new Set(["risk", "sentiment", "events", "bell"]);
const iconTone = (name) => accentIcons.has(name) ? "accent" : "neutral";

// Decorative icons keep the visible text as the accessible name of every control.
export default function Icon({ name, tone = iconTone(name), className = "" }) {
  return <svg className={`ui-icon icon-tone-${tone} ${className}`} viewBox="0 0 24 24" aria-hidden="true" focusable="false">{ICONS[name] ?? ICONS.info}</svg>;
}

export function IconBadge({ name, tone = iconTone(name), className = "" }) {
  return <span className={`icon-badge icon-tone-${tone} ${className}`} aria-hidden="true"><Icon name={name} tone="inherit" /></span>;
}
