import { seoulDateKey } from "./briefingPeriodSession";
import DatePicker from "./DatePicker";

export default function AnalysisPeriodControl({ period, onChange }) {
  const todayKey = seoulDateKey();

  return <div className="briefing-date-range" role="group" aria-label="조회 기간">
    <div className="briefing-date-field"><span>시작일</span><DatePicker label="시작일" value={period.start} max={todayKey} onChange={(value) => onChange("start", value)} /></div>
    <span className="briefing-date-separator" aria-hidden="true">~</span>
    <div className="briefing-date-field"><span>종료일</span><DatePicker label="종료일" value={period.end} min={period.start} max={todayKey} onChange={(value) => onChange("end", value)} /></div>
  </div>;
}
