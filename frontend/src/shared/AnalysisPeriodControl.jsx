import { seoulDateKey } from "./briefingPeriodSession";

export default function AnalysisPeriodControl({ period, onChange }) {
  const todayKey = seoulDateKey();

  return <div className="briefing-date-range" role="group" aria-label="조회 기간">
    <label><span>조회 기간</span><input type="date" aria-label="시작일" value={period.start} max={todayKey} onChange={(event) => onChange("start", event.target.value)} /></label>
    <span className="briefing-date-separator" aria-hidden="true">~</span>
    <label><input type="date" aria-label="종료일" value={period.end} min={period.start} max={todayKey} onChange={(event) => onChange("end", event.target.value)} /></label>
  </div>;
}
