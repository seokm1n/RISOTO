// Date-only arithmetic uses UTC so month boundaries do not depend on DST or the host timezone.
export const dateKey = (date) => date.toISOString().slice(0, 10);
export const parseDate = (key) => new Date(`${key}T00:00:00Z`);

export function shiftDays(key, count) {
  const date = parseDate(key);
  date.setUTCDate(date.getUTCDate() + count);
  return dateKey(date);
}

export function shiftMonths(key, count) {
  const date = parseDate(key);
  const day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + count);
  const last = new Date(date);
  last.setUTCMonth(last.getUTCMonth() + 1, 0);
  date.setUTCDate(Math.min(day, last.getUTCDate()));
  return dateKey(date);
}

export function clampDate(key, min, max) {
  return min && key < min ? min : max && key > max ? max : key;
}

export function calendarDays(month) {
  const first = `${month}-01`;
  const start = shiftDays(first, -parseDate(first).getUTCDay());
  return Array.from({ length: 42 }, (_, index) => shiftDays(start, index));
}

export function monthHasDates(month, min, max) {
  const first = `${month}-01`;
  const last = shiftDays(shiftMonths(first, 1), -1);
  return (!min || last >= min) && (!max || first <= max);
}
