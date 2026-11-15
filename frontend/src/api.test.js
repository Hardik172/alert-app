import { describe, expect, it, vi } from "vitest";
import { api, matchesFilter } from "./api";

const alert = {
  id: 1, clientName: "John Smith", address: "221B Baker Street", raisedBy: "Sarah Lee",
  alertType: "AML", status: "Open",
};

function stubFetch(response) {
  const fetch = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const okJson = (body) => ({ ok: true, status: 200, json: async () => body });

describe("matchesFilter", () => {
  it("matches when no filters are set", () => {
    expect(matchesFilter(alert, {})).toBe(true);
    expect(matchesFilter(alert, { search: "", alertType: "", status: "" })).toBe(true);
  });

  it("filters by alert type and status", () => {
    expect(matchesFilter(alert, { alertType: "AML" })).toBe(true);
    expect(matchesFilter(alert, { alertType: "PEP" })).toBe(false);
    expect(matchesFilter(alert, { status: "Open" })).toBe(true);
    expect(matchesFilter(alert, { status: "Closed" })).toBe(false);
  });

  it("searches name, address and raised-by case-insensitively", () => {
    expect(matchesFilter(alert, { search: "SMITH" })).toBe(true);
    expect(matchesFilter(alert, { search: "baker" })).toBe(true);
    expect(matchesFilter(alert, { search: "sarah" })).toBe(true);
    expect(matchesFilter(alert, { search: "garcia" })).toBe(false);
  });

  it("requires all filters to match", () => {
    expect(matchesFilter(alert, { search: "smith", status: "Closed" })).toBe(false);
  });
});

describe("GraphQL client", () => {
  it("POSTs to /graphql with the query and variables", async () => {
    const fetch = stubFetch(okJson({ data: { alerts: [] } }));
    await api.alerts({ search: "chen" });
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe("/graphql");
    expect(options.method).toBe("POST");
    expect(options.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(options.body);
    expect(body.query).toMatch(/query Alerts/);
    expect(body.variables).toEqual({ filter: { search: "chen" } });
  });

  it("drops empty filter values", async () => {
    const fetch = stubFetch(okJson({ data: { alerts: [] } }));
    await api.alerts({ search: "", alertType: "Fraud", status: "" });
    expect(JSON.parse(fetch.mock.calls[0][1].body).variables).toEqual({ filter: { alertType: "Fraud" } });
  });

  it("initial load asks for alerts, stats and meta in one request", async () => {
    const fetch = stubFetch(okJson({ data: { alerts: [], stats: {}, meta: {} } }));
    await api.initialLoad({});
    expect(fetch).toHaveBeenCalledTimes(1);
    const { query } = JSON.parse(fetch.mock.calls[0][1].body);
    expect(query).toMatch(/alerts\(/);
    expect(query).toMatch(/stats\s*{/);
    expect(query).toMatch(/meta\s*{/);
  });

  it("mutations request the changed alert, fresh stats and the audit entry", async () => {
    const fetch = stubFetch(okJson({ data: { updateAlert: { alert: alert, stats: {} } } }));
    const res = await api.updateAlert(1, { status: "Closed" });
    const body = JSON.parse(fetch.mock.calls[0][1].body);
    expect(body.query).toMatch(/updateAlert\(id: \$id, input: \$input\) \{\s*alert \{[\s\S]*stats[\s\S]*auditEntry/);
    expect(body.variables).toEqual({ id: 1, input: { status: "Closed" } });
    expect(res.alert).toEqual(alert);
  });

  it("unwraps mutation payloads", async () => {
    stubFetch(okJson({ data: { deleteAlert: { id: 4, stats: { total: 2 } } } }));
    expect(await api.deleteAlert(4)).toEqual({ id: 4, stats: { total: 2 } });
  });

  it("throws GraphQL error messages", async () => {
    stubFetch(okJson({ data: null, errors: [{ message: "Bad status" }, { message: "Also bad" }] }));
    await expect(api.updateAlert(1, { status: "x" })).rejects.toThrow("Bad status; Also bad");
  });

  it("throws on HTTP errors with no JSON body", async () => {
    stubFetch({ ok: false, status: 502, json: async () => { throw new Error("not json"); } });
    await expect(api.alerts({})).rejects.toThrow("Request failed (502)");
  });

  it("passes the abort signal through", async () => {
    const fetch = stubFetch(okJson({ data: { alerts: [] } }));
    const ctrl = new AbortController();
    await api.alerts({}, { signal: ctrl.signal });
    expect(fetch.mock.calls[0][1].signal).toBe(ctrl.signal);
  });
});

describe("auth headers", () => {
  it("sends the stored token as a Bearer header, and none when signed out", async () => {
    const { session } = await import("./api");
    const fetch = stubFetch(okJson({ data: { alerts: [] } }));
    await api.alerts({});
    expect(fetch.mock.calls[0][1].headers.Authorization).toBeUndefined();

    session.set("abc");
    await api.alerts({});
    expect(fetch.mock.calls[1][1].headers.Authorization).toBe("Bearer abc");
    session.set(null);
    expect(localStorage.getItem("client-alerts-token")).toBeNull();
  });

  it("exposes the server error code on thrown errors", async () => {
    stubFetch(okJson({ data: null, errors: [{ message: "Not signed in", extensions: { code: "UNAUTHENTICATED" } }] }));
    await expect(api.alerts({})).rejects.toMatchObject({ message: "Not signed in", code: "UNAUTHENTICATED" });
  });
});
