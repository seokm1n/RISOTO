const BRIEFING_PERIOD_KEY = "risoto:briefing-period";
export const BRIEFING_PERIOD_CHANGED_EVENT = "risoto:briefing-period-change";

export function seoulDateKey(value = new Date()) {
  return value.toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
}

export function isValidBriefingDate(value) {
  return typeof value === "string"
    && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString().slice(0, 10) === value;
}

export function isValidBriefingPeriod(period) {
  return isValidBriefingDate(period?.start)
    && isValidBriefingDate(period?.end)
    && period.start <= period.end
    && period.end <= seoulDateKey();
}

export function getBriefingPeriod() {
  const end = seoulDateKey();
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(BRIEFING_PERIOD_KEY));
    if (isValidBriefingPeriod(saved)) {
      return { start: saved.start, end: saved.end };
    }
  } catch {
    // 저장소를 사용할 수 없거나 값이 잘못된 경우 최근 7일을 사용한다.
  }
  const start = new Date(`${end}T00:00:00+09:00`);
  start.setUTCDate(start.getUTCDate() - 6);
  return { start: seoulDateKey(start), end };
}

export function saveBriefingPeriod(period) {
  if (!isValidBriefingPeriod(period)) return;
  const saved = { start: period.start, end: period.end };
  try {
    window.sessionStorage.setItem(BRIEFING_PERIOD_KEY, JSON.stringify(saved));
  } catch {
    // 세션 저장소가 차단되어도 기간 선택과 조회는 계속 동작한다.
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(BRIEFING_PERIOD_CHANGED_EVENT, { detail: saved }));
  }
}
