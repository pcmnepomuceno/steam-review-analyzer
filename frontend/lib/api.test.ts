import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchState, pageInfo, poll, type Loaded } from "./api";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const PROCESSING = { appid: 1, status: "processing", detail: "retry shortly" };
const READY = { appid: 1, status: "ready" };

function stubFetch(...responses: (Response | Error)[]) {
  const fetchMock = vi.fn(async () => {
    const next = responses.shift();
    if (!next) throw new Error("unexpected extra fetch");
    if (next instanceof Error) throw next;
    return next;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("fetchState", () => {
  it("maps 200 to ready with the body", async () => {
    stubFetch(json(200, READY));
    expect(await fetchState("/x")).toEqual({ kind: "ready", data: READY });
  });

  it("maps 404 to not_found with the backend detail", async () => {
    stubFetch(json(404, { detail: "Steam has no app with appid 999" }));
    expect(await fetchState("/x")).toEqual({
      kind: "not_found",
      message: "Steam has no app with appid 999",
    });
  });

  it("maps 422 (too few reviews) to an error with the detail", async () => {
    stubFetch(json(422, { detail: "App 5 has only 50 usable English reviews" }));
    expect(await fetchState("/x")).toEqual({
      kind: "error",
      status: 422,
      message: "App 5 has only 50 usable English reviews",
    });
  });

  it("maps 403 unavailable to the list of pre-processed games", async () => {
    const available = [{ appid: 620, name: "Portal 2" }];
    stubFetch(json(403, { status: "unavailable", detail: "demo only", available }), json(403, {}));
    expect(await fetchState("/x")).toEqual({ kind: "unavailable", message: "demo only", games: available });
    expect((await fetchState("/x")).kind).toBe("error"); // any other 403
  });

  it("tells a failed analysis apart from other 500s", async () => {
    stubFetch(
      json(500, { status: "failed", detail: "run --force", retryable: false, out_of_memory: false }),
      json(500, { status: "failed", detail: "generic", retryable: true, out_of_memory: true }),
      json(500, { status: "failed", detail: "from an older API" }),
      json(500, "oops"),
    );
    expect(await fetchState("/x")).toEqual({
      kind: "failed", message: "run --force", retryable: false, outOfMemory: false,
    });
    expect(await fetchState("/x")).toEqual({
      kind: "failed", message: "generic", retryable: true, outOfMemory: true,
    });
    // No flags (an API older than the retry button): no button, no memory claim.
    expect(await fetchState("/x")).toMatchObject({
      kind: "failed", retryable: false, outOfMemory: false,
    });
    expect(await fetchState("/x")).toMatchObject({ kind: "error", status: 500 });
  });

  it("maps a network failure to an error", async () => {
    stubFetch(new TypeError("fetch failed"));
    expect(await fetchState("/x")).toMatchObject({ kind: "error", status: null });
  });

  it("maps a 200 with an unreadable body to an error, not ready", async () => {
    stubFetch(new Response("<html>proxy error</html>", { status: 200 }));
    expect(await fetchState("/x")).toMatchObject({ kind: "error", status: 200 });
  });

  it("rethrows when aborted while the body is read", async () => {
    const controller = new AbortController();
    const res = json(200, READY);
    vi.spyOn(res, "json").mockImplementation(async () => {
      controller.abort();
      throw new DOMException("aborted", "AbortError");
    });
    stubFetch(res);
    await expect(fetchState("/x", controller.signal)).rejects.toBeDefined();
  });
});

describe("poll", () => {
  it("keeps polling while processing, then returns ready", async () => {
    const fetchMock = stubFetch(json(202, PROCESSING), json(202, PROCESSING), json(200, READY));
    const seen: Loaded<unknown>["kind"][] = [];

    const result = await poll("/x", { intervalMs: 0, onUpdate: (s) => seen.push(s.kind) });

    expect(result).toEqual({ kind: "ready", data: READY });
    expect(seen).toEqual(["processing", "processing", "ready"]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("stops polling when processing turns into failed", async () => {
    const fetchMock = stubFetch(json(202, PROCESSING), json(500, { status: "failed", detail: "x" }));
    expect((await poll("/x", { intervalMs: 0 })).kind).toBe("failed");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not poll again after not_found", async () => {
    const fetchMock = stubFetch(json(404, { detail: "nope" }));
    expect((await poll("/x", { intervalMs: 0 })).kind).toBe("not_found");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("gives up with timeout when processing never ends", async () => {
    const fetchMock = stubFetch(json(202, PROCESSING), json(202, PROCESSING));
    const result = await poll("/x", { intervalMs: 10, maxWaitMs: 15 });
    expect(result).toEqual({ kind: "timeout" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops when aborted between polls", async () => {
    const fetchMock = stubFetch(json(202, PROCESSING));
    const controller = new AbortController();
    const pending = poll("/x", { intervalMs: 60_000, signal: controller.signal });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    controller.abort();
    await expect(pending).rejects.toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("pageInfo", () => {
  it("handles the last, partial page", () => {
    expect(pageInfo(45, 20, 40)).toEqual({
      from: 41,
      to: 45,
      hasPrev: true,
      prevOffset: 20,
      hasNext: false,
    });
  });

  it("handles an exact last page", () => {
    expect(pageInfo(40, 20, 20)).toMatchObject({ to: 40, hasNext: false });
  });

  it("handles an empty result", () => {
    expect(pageInfo(0, 20, 0)).toEqual({
      from: 0,
      to: 0,
      hasPrev: false,
      prevOffset: 0,
      hasNext: false,
    });
  });

  it("points an offset past the end back at the last page", () => {
    expect(pageInfo(45, 20, 5000)).toEqual({
      from: 0,
      to: 0,
      hasPrev: true,
      prevOffset: 40,
      hasNext: false,
    });
    expect(pageInfo(40, 20, 40)).toMatchObject({ to: 0, prevOffset: 20 });
  });
});
