import { afterEach, describe, expect, it, vi } from "vitest";
import { addNote, load, scoreRegion } from "../lib/api";

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

describe("load(): API first, static fallback", () => {
  it("uses the backend when it answers with JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ from: "api" })));
    expect(await load("/api/index", "/data/index.json")).toEqual({ data: { from: "api" }, source: "api" });
  });

  it("falls back when /api returns an HTML 404 (Firebase without a backend)", async () => {
    const fetch = vi.fn(async (url: string) =>
      url.startsWith("/api") ? new Response("<html>404</html>", { status: 404, headers: { "content-type": "text/html" } })
        : jsonResponse({ from: "static" }));
    vi.stubGlobal("fetch", fetch);
    expect(await load("/api/index", "/data/index.json")).toEqual({ data: { from: "static" }, source: "static" });
  });

  it("falls back when the backend is down", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api")) throw new TypeError("Failed to fetch");
      return jsonResponse({ from: "static" });
    }));
    expect((await load("/api/index", "/data/index.json")).source).toBe("static");
  });
});

describe("scoreRegion() and addNote()", () => {
  it("sends weights as w.<feature> query parameters", async () => {
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ columns: {}, candidates: [] }));
    vi.stubGlobal("fetch", fetch);
    await scoreRegion("mars_jezero", "haiti-northwest", { albedo: 2, slope_mean: 0 });
    const url = new URL(fetch.mock.calls[0][0] as string, "http://x");
    expect(url.pathname).toBe("/api/score");
    expect(Object.fromEntries(url.searchParams)).toEqual(
      { target: "mars_jezero", region: "haiti-northwest", "w.albedo": "2", "w.slope_mean": "0" });
  });

  it("surfaces the backend's error message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ error: "missing or wrong X-Team-Token" }, 403)));
    await expect(addNote({ target_id: "", region: "r", cell: 1, lat: 0, lon: 0, author: "a", text: "t" }, "bad"))
      .rejects.toThrow("missing or wrong X-Team-Token");
  });

  it("sends the team token header", async () => {
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse({ id: 1 }, 201));
    vi.stubGlobal("fetch", fetch);
    await addNote({ target_id: "", region: "r", cell: 1, lat: 0, lon: 0, author: "a", text: "t" }, "secret");
    const init = fetch.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["x-team-token"]).toBe("secret");
    expect(init.method).toBe("POST");
  });
});
