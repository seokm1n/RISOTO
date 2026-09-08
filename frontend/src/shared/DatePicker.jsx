import Icon from "./Icon";
import { useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { isValidBriefingDate, seoulDateKey } from "./briefingPeriodSession";
import { calendarDays, clampDate, monthHasDates, parseDate, shiftDays, shiftMonths } from "./calendarDate";
import "./DatePicker.css";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];
const spokenDate = (key) => {
  const date = parseDate(key);
  return `${date.getUTCFullYear()}년 ${date.getUTCMonth() + 1}월 ${date.getUTCDate()}일 ${WEEKDAYS[date.getUTCDay()]}요일`;
};

export default function DatePicker({ value, onChange, min, max, label = "날짜", id, disabled = false }) {
  const uniqueId = useId();
  const triggerId = id ?? `date-picker-${uniqueId}`;
  const dialogId = `${triggerId}-calendar`;
  const headingId = `${triggerId}-heading`;
  const triggerRef = useRef(null);
  const dialogRef = useRef(null);
  const [open, setOpen] = useState(false);
  const today = seoulDateKey();
  const lower = isValidBriefingDate(min) ? min : "1900-01-01";
  const upper = isValidBriefingDate(max) ? max : `${Number(today.slice(0, 4)) + 20}-12-31`;
  const initial = clampDate(isValidBriefingDate(value) ? value : today, lower, upper);
  const [focusedDate, setFocusedDate] = useState(initial);
  const [month, setMonth] = useState(initial.slice(0, 7));
  const focusDayAfterRender = useRef(false);
  const days = calendarDays(month);
  const year = Number(month.slice(0, 4));
  const monthNumber = Number(month.slice(5, 7));
  const years = Array.from({ length: Math.max(0, Number(upper.slice(0, 4)) - Number(lower.slice(0, 4)) + 1) }, (_, index) => Number(lower.slice(0, 4)) + index);
  const allowed = (key) => key >= lower && key <= upper;

  const close = () => {
    dialogRef.current?.close();
    setOpen(false);
  };
  const choose = (key) => {
    if (!allowed(key)) return;
    onChange(key);
    close();
  };
  const show = () => {
    setFocusedDate(initial);
    setMonth(initial.slice(0, 7));
    focusDayAfterRender.current = true;
    setOpen(true);
  };

  useLayoutEffect(() => {
    if (!open) return undefined;
    const dialog = dialogRef.current;
    const trigger = triggerRef.current;
    dialog.showModal();
    const position = () => {
      const rect = trigger.getBoundingClientRect();
      const viewport = window.visualViewport;
      const width = viewport?.width ?? window.innerWidth;
      const height = viewport?.height ?? window.innerHeight;
      const offsetLeft = viewport?.offsetLeft ?? 0;
      const offsetTop = viewport?.offsetTop ?? 0;
      const left = Math.max(offsetLeft + 12, Math.min(rect.left, offsetLeft + width - dialog.offsetWidth - 12));
      const below = rect.bottom + 8;
      const top = below + dialog.offsetHeight <= offsetTop + height - 12
        ? below : Math.max(offsetTop + 12, rect.top - dialog.offsetHeight - 8);
      dialog.style.left = `${left}px`;
      dialog.style.top = `${top}px`;
    };
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    window.visualViewport?.addEventListener("resize", position);
    return () => {
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
      window.visualViewport?.removeEventListener("resize", position);
      if (dialog.open) dialog.close();
      trigger.focus({ preventScroll: true });
    };
  }, [open]);

  useLayoutEffect(() => {
    if (open && focusDayAfterRender.current) {
      dialogRef.current?.querySelector(`[data-date="${focusedDate}"]`)?.focus({ preventScroll: true });
      focusDayAfterRender.current = false;
    }
  }, [open, focusedDate, month]);

  const moveFocus = (key) => {
    const next = clampDate(key, lower, upper);
    focusDayAfterRender.current = true;
    setMonth(next.slice(0, 7));
    setFocusedDate(next);
  };
  const changeMonth = (nextMonth) => {
    const next = clampDate(`${nextMonth}-01`, lower, upper);
    setMonth(next.slice(0, 7));
    setFocusedDate(next);
  };
  const navigateDays = (event) => {
    const dayOfWeek = parseDate(focusedDate).getUTCDay();
    const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -dayOfWeek, End: 6 - dayOfWeek };
    if (event.key in offsets) {
      event.preventDefault();
      moveFocus(shiftDays(focusedDate, offsets[event.key]));
    } else if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      moveFocus(shiftMonths(focusedDate, (event.key === "PageUp" ? -1 : 1) * (event.shiftKey ? 12 : 1)));
    }
  };

  return <div className="date-picker">
    <button className="date-picker-trigger" type="button" id={triggerId} ref={triggerRef}
      disabled={disabled || lower > upper} onClick={show} aria-label={`${label}: ${isValidBriefingDate(value) ? spokenDate(value) : "날짜 선택"}`}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? dialogId : undefined}>
      <span>{isValidBriefingDate(value) ? value.replaceAll("-", ". ") : "날짜 선택"}</span>
      <Icon name="calendar" />
    </button>
    {open && createPortal(<dialog className="date-picker-dialog" ref={dialogRef} id={dialogId} aria-labelledby={headingId}
      onCancel={(event) => { event.preventDefault(); close(); }} onClose={(event) => { if (!event.currentTarget.open) setOpen(false); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
      }}>
      <header className="date-picker-title"><strong id={headingId}>{label} 선택</strong><button type="button" className="date-picker-icon" aria-label="달력 닫기" onClick={close}>×</button></header>
      <div className="date-picker-month-bar">
        <button className="date-picker-icon" type="button" aria-label="이전 달" disabled={!monthHasDates(shiftMonths(`${month}-01`, -1).slice(0, 7), lower, upper)} onClick={() => changeMonth(shiftMonths(`${month}-01`, -1).slice(0, 7))}>‹</button>
        <div className="date-picker-month-selects">
          <select aria-label="연도" value={year} onChange={(event) => changeMonth(`${event.target.value}-${String(monthNumber).padStart(2, "0")}`)}>{years.map(item => <option key={item} value={item}>{item}년</option>)}</select>
          <select aria-label="월" value={monthNumber} onChange={(event) => changeMonth(`${year}-${String(event.target.value).padStart(2, "0")}`)}>{Array.from({ length: 12 }, (_, index) => index + 1).map(item => <option key={item} value={item} disabled={!monthHasDates(`${year}-${String(item).padStart(2, "0")}`, lower, upper)}>{item}월</option>)}</select>
        </div>
        <button className="date-picker-icon" type="button" aria-label="다음 달" disabled={!monthHasDates(shiftMonths(`${month}-01`, 1).slice(0, 7), lower, upper)} onClick={() => changeMonth(shiftMonths(`${month}-01`, 1).slice(0, 7))}>›</button>
      </div>
      <span className="date-picker-sr-only" aria-live="polite">{year}년 {monthNumber}월</span>
      <table className="date-picker-grid" role="grid" aria-label={`${year}년 ${monthNumber}월`}>
        <thead><tr>{WEEKDAYS.map(day => <th scope="col" key={day}>{day}</th>)}</tr></thead>
        <tbody>{Array.from({ length: 6 }, (_, week) => <tr key={week}>{days.slice(week * 7, week * 7 + 7).map(key => <td key={key} aria-selected={key === value}>
          <button type="button" data-date={key} disabled={!allowed(key)} tabIndex={key === focusedDate ? 0 : -1}
            aria-label={spokenDate(key)} aria-current={key === today ? "date" : undefined}
            className={`date-picker-day${key === value ? " selected" : ""}${key === today ? " today" : ""}${key.slice(0, 7) !== month ? " outside" : ""}`}
            onFocus={() => setFocusedDate(key)} onKeyDown={navigateDays} onClick={() => choose(key)}>{Number(key.slice(8))}</button>
        </td>)}</tr>)}</tbody>
      </table>
      <footer className="date-picker-footer"><span>날짜를 선택하면 적용됩니다.</span><button type="button" disabled={!allowed(today)} onClick={() => choose(today)}>오늘</button></footer>
    </dialog>, document.body)}
  </div>;
}
