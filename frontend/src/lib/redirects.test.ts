import { describe, expect, it } from "vitest";
import { safeNext } from "./redirects";

describe("safeNext", () => {
  it.each([
    ["/app", "/app"],
    ["/app/settings", "/app/settings"],
    ["/app/bookings?scope=past", "/app/bookings?scope=past"],
    ["%2Fapp%2Fsettings", "/app/settings"],
  ])("allows %s", (input, out) => expect(safeNext(input)).toBe(out));

  it.each([
    "https://evil.example/app",
    "//evil.example/app",
    "/\\evil.example",
    "javascript:alert(1)",
    "/admin",
    "/apple",
    "/app/../sign-in",
    "%2F%2Fevil.example",
    "",
    null,
    "/app\u0000",
  ])("rejects %s", (input) => expect(safeNext(input as string)).toBe("/app"));
});
