import { describe, expect, it } from "vitest";
import type { PersonalEvent } from "@/v2/calendarData";
import { personalCalendarICS } from "@/v2/calendarExport";

const event: PersonalEvent = {
  id: "personal-1",
  personal: true,
  title: "中文日程,讨论;题目\\路径\n新行",
  startAt: "2026-11-28T10:00:00+08:00",
  endAt: "2026-11-28T10:30:00+08:00",
  courseName: "个人日程",
};
describe("Windows ICS 兼容", () => {
  it("定时事件保留绝对时间，文本转义防止插入事件", () => {
    const result = personalCalendarICS(
      [event],
      new Date("2026-11-28T01:00:00Z"),
    );
    expect(result).toContain(
      "DTSTART:20261128T020000Z\r\nDTEND:20261128T023000Z",
    );
    expect(result).toContain("SUMMARY:中文日程\\,讨论\\;题目\\\\路径\\n新行");
    expect(result).toContain("DTSTAMP:20261128T010000Z");
    expect(result.match(/BEGIN:VEVENT/g)).toHaveLength(1);
    expect(result).not.toContain("VALARM");
  });
  it("全天事件使用本机日期与排他结束日，不受 UTC 日期偏移影响", () => {
    const start = new Date(2026, 10, 28),
      end = new Date(2026, 10, 29);
    const result = personalCalendarICS([
      {
        ...event,
        allDay: true,
        startAt: start.toISOString(),
        endAt: end.toISOString(),
      },
    ]);
    expect(result).toContain(
      "DTSTART;VALUE=DATE:20261128\r\nDTEND;VALUE=DATE:20261129",
    );
  });
  it("长中文按 UTF-8 字节折行，恢复后正文完整，UID 稳定", () => {
    const title = "这是一段很长的中文🙂".repeat(40);
    const result = personalCalendarICS([{ ...event, title }]);
    for (const line of result.split("\r\n"))
      expect(new TextEncoder().encode(line).length).toBeLessThanOrEqual(75);
    expect(result.replace(/\r\n /g, "")).toContain(`SUMMARY:${title}`);
    expect(result).toContain("UID:personal-1@sjtu-learning-assistant.local");
    expect(personalCalendarICS([{ ...event, title: "更新" }])).toContain(
      "UID:personal-1@sjtu-learning-assistant.local",
    );
  });
  it("无效和倒序日期阻止导出，不伪造日期", () => {
    expect(() =>
      personalCalendarICS([{ ...event, startAt: "invalid" }]),
    ).toThrow();
    expect(() =>
      personalCalendarICS([{ ...event, endAt: event.startAt }]),
    ).toThrow();
  });
});
