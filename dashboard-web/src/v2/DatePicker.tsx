import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AIFloatingMenu } from "@/components/AIFloatingMenu";
import { dateKey } from "./calendarData";

function parseDate(value: string) {
  const date = new Date(`${value}T12:00:00`);
  return /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    !Number.isNaN(date.getTime()) &&
    dateKey(date) === value
    ? date
    : null;
}

/** 使用应用月历选择日期，文本输入保留键盘编辑能力。 */
export function V2DatePicker({
  value,
  onChange,
  label,
  min,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  min?: string;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(() => parseDate(value) || new Date());
  useEffect(() => {
    input.current?.setCustomValidity(
      parseDate(value) && (!min || value >= min)
        ? ""
        : "请填写有效日期，结束日期不能早于开始日期。",
    );
  }, [value, min]);
  const first = new Date(month.getFullYear(), month.getMonth(), 1, 12);
  const begin = new Date(first);
  begin.setDate(1 - ((first.getDay() + 6) % 7));
  const dates = Array.from({ length: 42 }, (_, i) => {
    const day = new Date(begin);
    day.setDate(begin.getDate() + i);
    return day;
  });
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  const choose = (key: string) => {
    onChange(key);
    close();
  };
  const toggle = () => {
    if (open) {
      close();
      return;
    }
    setMonth(parseDate(value) || new Date());
    setOpen(true);
    requestAnimationFrame(() =>
      (
        panel.current?.querySelector<HTMLButtonElement>(
          '[aria-pressed="true"]:not(:disabled)',
        ) ||
        panel.current?.querySelector<HTMLButtonElement>(
          ".v2-date-grid button:not(:disabled)",
        )
      )?.focus(),
    );
  };
  const shiftMonth = (by: number) =>
    setMonth(new Date(month.getFullYear(), month.getMonth() + by, 1, 12));
  return (
    <div className="v2-date-input">
      <input
        ref={input}
        type="text"
        aria-label={label}
        value={value}
        required
        pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}"
        placeholder="YYYY-MM-DD"
        onChange={(event) => {
          event.target.setCustomValidity(
            parseDate(event.target.value) && (!min || event.target.value >= min)
              ? ""
              : "请填写有效日期，结束日期不能早于开始日期。",
          );
          onChange(event.target.value);
        }}
      />
      <button
        type="button"
        ref={trigger}
        aria-label={`选择${label}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={toggle}
      >
        <CalendarDays size={16} />
      </button>
      {open && (
        <AIFloatingMenu
          anchorRef={trigger}
          onClose={close}
          width={300}
          className="v2-date-popover"
        >
          <div
            ref={panel}
            role="dialog"
            aria-label={`${label}选择器`}
            onKeyDown={(event) => {
              if (event.key === "Tab") {
                const controls = Array.from(
                  panel.current?.querySelectorAll<HTMLButtonElement>(
                    "button:not(:disabled)",
                  ) || [],
                );
                const current = controls.indexOf(
                  document.activeElement as HTMLButtonElement,
                );
                if (
                  (!event.shiftKey && current === controls.length - 1) ||
                  (event.shiftKey && current === 0)
                ) {
                  event.preventDefault();
                  close();
                }
                return;
              }
              const key = (event.target as HTMLElement).dataset.date;
              if (!key) return;
              const delta = {
                ArrowLeft: -1,
                ArrowRight: 1,
                ArrowUp: -7,
                ArrowDown: 7,
              }[event.key];
              if (!delta) return;
              event.preventDefault();
              const next = parseDate(key)!;
              next.setDate(next.getDate() + delta);
              const nextKey = dateKey(next);
              if (min && nextKey < min) return;
              if (!dates.some((date) => dateKey(date) === nextKey))
                setMonth(next);
              requestAnimationFrame(() =>
                panel.current
                  ?.querySelector<HTMLButtonElement>(`[data-date="${nextKey}"]`)
                  ?.focus(),
              );
            }}
          >
            <header>
              <button
                type="button"
                aria-label="上个月"
                onClick={() => shiftMonth(-1)}
              >
                <ChevronLeft size={16} />
              </button>
              <strong>
                {month.getFullYear()}年 {month.getMonth() + 1}月
              </strong>
              <button
                type="button"
                aria-label="下个月"
                onClick={() => shiftMonth(1)}
              >
                <ChevronRight size={16} />
              </button>
            </header>
            <div className="v2-date-grid">
              {["一", "二", "三", "四", "五", "六", "日"].map((day) => (
                <span key={day}>{day}</span>
              ))}
              {dates.map((day) => {
                const key = dateKey(day);
                return (
                  <button
                    type="button"
                    key={key}
                    data-date={key}
                    aria-label={key}
                    aria-pressed={key === value}
                    disabled={Boolean(min && key < min)}
                    className={
                      day.getMonth() !== month.getMonth() ? "outside" : ""
                    }
                    onClick={() => choose(key)}
                  >
                    {day.getDate()}
                  </button>
                );
              })}
            </div>
            <footer>
              <button
                type="button"
                disabled={Boolean(min && dateKey(new Date()) < min)}
                onClick={() => choose(dateKey(new Date()))}
              >
                今天
              </button>
              <button type="button" onClick={close}>
                取消
              </button>
            </footer>
          </div>
        </AIFloatingMenu>
      )}
    </div>
  );
}
