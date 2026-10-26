const SITE_TZ = "Etc/GMT-5"; // the observatory's clock (UTC+5)

export function fmtWhen(startIso: string, endIso: string, h24 = false): string {
  const opt: Intl.DateTimeFormatOptions = { timeZone: SITE_TZ, hour: "numeric", minute: "2-digit", hour12: !h24 };
  const d = new Intl.DateTimeFormat("en-GB", { timeZone: SITE_TZ, weekday: "short", day: "numeric", month: "short" });
  const t = new Intl.DateTimeFormat("en-US", opt);
  return `${d.format(new Date(startIso))}, ${t.format(new Date(startIso))} – ${t.format(new Date(endIso))}`;
}
