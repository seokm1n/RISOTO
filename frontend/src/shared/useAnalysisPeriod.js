import { useCallback, useEffect, useRef, useState } from "react";

import {
  BRIEFING_PERIOD_CHANGED_EVENT,
  getBriefingPeriod,
  isValidBriefingDate,
  isValidBriefingPeriod,
  saveBriefingPeriod,
  seoulDateKey,
} from "./briefingPeriodSession";

export function useAnalysisPeriod() {
  const [period, setPeriod] = useState(getBriefingPeriod);
  const periodRef = useRef(period);

  useEffect(() => {
    const restorePeriod = (event) => {
      const next = event?.detail ?? getBriefingPeriod();
      if (!isValidBriefingPeriod(next)) return;
      periodRef.current = next;
      setPeriod((current) => current.start === next.start && current.end === next.end ? current : next);
    };
    window.addEventListener(BRIEFING_PERIOD_CHANGED_EVENT, restorePeriod);
    const restored = getBriefingPeriod();
    restorePeriod({ detail: restored });
    saveBriefingPeriod(restored);
    return () => window.removeEventListener(BRIEFING_PERIOD_CHANGED_EVENT, restorePeriod);
  }, []);

  const changePeriod = useCallback((field, value) => {
    if ((field !== "start" && field !== "end") || !isValidBriefingDate(value) || value > seoulDateKey()) return;
    const next = { ...periodRef.current, [field]: value };
    if (next.start > next.end) next[field === "start" ? "end" : "start"] = value;
    periodRef.current = next;
    setPeriod(next);
    // 페이지를 바로 이동해도 변경한 기간을 다음 화면에서 복원할 수 있도록 즉시 저장한다.
    saveBriefingPeriod(next);
  }, []);

  return { period, changePeriod };
}

export default useAnalysisPeriod;
