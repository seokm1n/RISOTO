import assert from "node:assert/strict";
import test from "node:test";
import { calendarDays, clampDate, monthHasDates, shiftDays, shiftMonths } from "./calendarDate.js";

test("month navigation preserves the day where possible and clamps at month end", () => {
  assert.equal(shiftMonths("2024-01-31", 1), "2024-02-29");
  assert.equal(shiftMonths("2025-01-31", 1), "2025-02-28");
  assert.equal(shiftMonths("2024-02-29", 12), "2025-02-28");
  assert.equal(shiftMonths("2026-01-15", -1), "2025-12-15");
});

test("day navigation crosses leap days, year boundaries, and DST dates", () => {
  assert.equal(shiftDays("2024-02-28", 1), "2024-02-29");
  assert.equal(shiftDays("2024-02-29", 1), "2024-03-01");
  assert.equal(shiftDays("2026-01-01", -1), "2025-12-31");
  assert.equal(shiftDays("2026-03-08", 1), "2026-03-09");
});

test("calendar includes six full Sunday-first weeks without duplicate dates", () => {
  const dates = calendarDays("2026-08");
  assert.equal(dates.length, 42);
  assert.equal(new Set(dates).size, 42);
  assert.equal(dates[0], "2026-07-26");
  assert.equal(dates[41], "2026-09-05");
  assert.ok(calendarDays("2024-02").includes("2024-02-29"));
});

test("date and month limits are inclusive, including a single-day interval", () => {
  assert.equal(clampDate("2026-09-01", "2026-09-03", "2026-09-08"), "2026-09-03");
  assert.equal(clampDate("2026-09-10", "2026-09-03", "2026-09-08"), "2026-09-08");
  assert.equal(clampDate("2026-09-05", "2026-09-03", "2026-09-08"), "2026-09-05");
  assert.equal(monthHasDates("2026-08", "2026-09-08", "2026-09-08"), false);
  assert.equal(monthHasDates("2026-09", "2026-09-08", "2026-09-08"), true);
  assert.equal(monthHasDates("2026-10", "2026-09-08", "2026-09-08"), false);
});
