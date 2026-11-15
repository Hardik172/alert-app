import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import { TOKEN_KEY } from "./api";
import { installMockServer, tokenFor } from "./test/mockServer";

// ---------- helpers ----------

/** Start the app already signed in (stored token) as the given role. */
async function renderApp(serverOptions, role = "Admin") {
  const server = installMockServer(serverOptions);
  localStorage.setItem(TOKEN_KEY, tokenFor(role));
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText("John Smith");
  return { server, user };
}

const statValue = (label) =>
  screen.getByText(label, { selector: ".stat-label" }).previousSibling.textContent;
const tableRow = (name) => screen.getByRole("row", { name: new RegExp(name) });
const queryTableRow = (name) => screen.queryByRole("row", { name: new RegExp(name) });
const panel = () => screen.getByRole("complementary");

async function openAlert(user, name) {
  await user.click(screen.getByText(name, { selector: "td" }));
  return panel();
}

// ---------- tests ----------

describe("initial load", () => {
  it("loads alerts, stats and dropdown options with a single request", async () => {
    const { server } = await renderApp();
    expect(server.ops()).toEqual(["InitialLoad"]);

    expect(statValue("Total alerts")).toBe("3");
    expect(statValue("Open")).toBe("1");
    expect(statValue("Under review")).toBe("1");
    expect(statValue("Closed")).toBe("1");
    expect(statValue("High severity (active)")).toBe("1");

    const typeFilter = screen.getByLabelText("Filter by alert type");
    expect(within(typeFilter).getByRole("option", { name: "KYC Incomplete" })).toBeInTheDocument();
  });

  it("shows every required client detail in the table", async () => {
    await renderApp();
    const row = tableRow("John Smith");
    for (const text of ["1980-04-12", "221B Baker Street, London", "United Kingdom", "AML", "High", "Sarah Lee", "Open"]) {
      expect(within(row).getByText(text)).toBeInTheDocument();
    }
  });

  it("lists alerts newest first", async () => {
    await renderApp();
    const names = screen.getAllByRole("row").slice(1).map((r) => r.cells[0].textContent);
    expect(names).toEqual(["Carlos Silva", "Maria Garcia", "John Smith"]);
  });

  it("shows an error when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Failed to fetch")));
    localStorage.setItem(TOKEN_KEY, tokenFor("Admin"));
    render(<App />);
    expect(await screen.findByText(/Could not reach the API: Failed to fetch/)).toBeInTheDocument();
  });

  it("shows an empty state when there are no alerts", async () => {
    installMockServer({ alerts: [] });
    localStorage.setItem(TOKEN_KEY, tokenFor("Admin"));
    render(<App />);
    expect(await screen.findByText("No alerts match your filters.")).toBeInTheDocument();
  });
});

describe("filtering", () => {
  it("debounces search and fetches only the list (not stats/meta)", async () => {
    const { server, user } = await renderApp();
    await user.type(screen.getByLabelText("Search alerts"), "chen");

    await waitFor(() => expect(queryTableRow("John Smith")).not.toBeInTheDocument());
    expect(tableRow("Maria Garcia")).toBeInTheDocument(); // raised by David Chen
    expect(server.ops()).toEqual(["InitialLoad", "Alerts"]); // 4 keystrokes -> 1 request
    expect(server.calls[1].variables).toEqual({ filter: { search: "chen" } });
  });

  it("filters by alert type and status", async () => {
    const { server, user } = await renderApp();
    await user.selectOptions(screen.getByLabelText("Filter by alert type"), "Fraud");
    await waitFor(() => expect(queryTableRow("John Smith")).not.toBeInTheDocument());
    expect(tableRow("Carlos Silva")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Filter by status"), "Open");
    expect(await screen.findByText("No alerts match your filters.")).toBeInTheDocument();
    expect(server.calls.at(-1).variables).toEqual({ filter: { alertType: "Fraud", status: "Open" } });
  });
});

describe("detail panel", () => {
  it("shows the full client details when a row is clicked", async () => {
    const { user } = await renderApp();
    const p = await openAlert(user, "Maria Garcia");
    expect(within(p).getByRole("heading", { name: "Maria Garcia" })).toBeInTheDocument();
    expect(within(p).getByText(/1975-09-30/)).toBeInTheDocument();
    expect(within(p).getByText(/\(\d+ yrs\)/)).toBeInTheDocument();
    expect(within(p).getByText("Calle de Alcalá 45, Madrid")).toBeInTheDocument();
    expect(within(p).getByText("Spain")).toBeInTheDocument();
    expect(within(p).getByText("David Chen")).toBeInTheDocument();
    expect(within(p).getByText("#2")).toBeInTheDocument();
    expect(tableRow("Maria Garcia")).toHaveClass("selected");
  });

  it("closes with the × button", async () => {
    const { user } = await renderApp();
    await openAlert(user, "John Smith");
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("updates status with one request and patches the row and stats in place", async () => {
    const { server, user } = await renderApp();
    const p = await openAlert(user, "John Smith");
    await user.selectOptions(within(p).getByLabelText("Status"), "Closed");

    await waitFor(() => expect(within(tableRow("John Smith")).getByText("Closed")).toBeInTheDocument());
    expect(statValue("Open")).toBe("0");
    expect(statValue("Closed")).toBe("2");
    expect(statValue("High severity (active)")).toBe("0");
    expect(server.ops()).toEqual(["InitialLoad", "UpdateAlert"]); // no refetch
    expect(server.calls[1].variables).toEqual({ id: 1, input: { status: "Closed" } });
  });

  it("removes an edited row that no longer matches the filter but keeps the panel open", async () => {
    const { user } = await renderApp();
    await user.selectOptions(screen.getByLabelText("Filter by status"), "Open");
    await waitFor(() => expect(queryTableRow("Maria Garcia")).not.toBeInTheDocument());

    const p = await openAlert(user, "John Smith");
    await user.selectOptions(within(p).getByLabelText("Status"), "Closed");
    expect(await screen.findByText("No alerts match your filters.")).toBeInTheDocument();
    expect(within(panel()).getByLabelText("Status")).toHaveValue("Closed");
  });

  it("enables Save notes only after notes change, then saves them", async () => {
    const { server, user } = await renderApp();
    const p = await openAlert(user, "Maria Garcia");
    const save = within(p).getByRole("button", { name: "Save notes" });
    expect(save).toBeDisabled();

    await user.type(within(p).getByLabelText("Notes"), "Spouse is a minister");
    expect(save).toBeEnabled();
    await user.click(save);

    await waitFor(() => expect(save).toBeDisabled());
    expect(server.calls.at(-1)).toMatchObject({ op: "UpdateAlert", variables: { id: 2, input: { notes: "Spouse is a minister" } } });
  });

  it("shows server errors from a failed update", async () => {
    const { server, user } = await renderApp();
    server.failNext("UpdateAlert", "Invalid status 'X'");
    const p = await openAlert(user, "John Smith");
    await user.selectOptions(within(p).getByLabelText("Severity"), "Low");
    expect(await screen.findByText("Invalid status 'X'")).toBeInTheDocument();
    expect(within(tableRow("John Smith")).getByText("High")).toBeInTheDocument(); // unchanged
  });

  it("requires confirmation before deleting", async () => {
    const { server, user } = await renderApp();
    const p = await openAlert(user, "Carlos Silva");

    await user.click(within(p).getByRole("button", { name: "Delete" }));
    await user.click(within(p).getByRole("button", { name: "Cancel" }));
    expect(server.ops()).not.toContain("DeleteAlert");

    await user.click(within(p).getByRole("button", { name: "Delete" }));
    await user.click(within(p).getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(queryTableRow("Carlos Silva")).not.toBeInTheDocument());
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    expect(statValue("Total alerts")).toBe("2");
    expect(server.ops()).toEqual(["InitialLoad", "DeleteAlert"]);
  });
});

describe("flagging a new client", () => {
  async function fillForm(user, overrides = {}) {
    const values = {
      "Client name": "Aisha Khan", Address: "12 Marine Drive, Mumbai",
      Country: "India", "Raised by": "Priya Patel", ...overrides,
    };
    for (const [label, value] of Object.entries(values)) {
      await user.clear(screen.getByLabelText(label));
      await user.type(screen.getByLabelText(label), value);
    }
    fireEvent.change(screen.getByLabelText("Date of birth"), { target: { value: "1990-06-21" } });
    await user.selectOptions(screen.getByLabelText("Type of alert"), "KYC Incomplete");
    await user.selectOptions(screen.getByLabelText("Severity"), "Low");
  }

  it("creates an alert with one request, adds the row and opens it", async () => {
    const { server, user } = await renderApp();
    await user.click(screen.getByRole("button", { name: "+ Flag client" }));
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Raise alert" }));

    await waitFor(() => expect(screen.queryByRole("heading", { name: "Flag a client" })).not.toBeInTheDocument());
    expect(tableRow("Aisha Khan")).toBeInTheDocument();
    expect(within(panel()).getByRole("heading", { name: "Aisha Khan" })).toBeInTheDocument();
    expect(statValue("Total alerts")).toBe("4");
    expect(server.ops()).toEqual(["InitialLoad", "CreateAlert"]);
    expect(server.calls[1].variables.input).toEqual({
      clientName: "Aisha Khan", dob: "1990-06-21", address: "12 Marine Drive, Mumbai",
      country: "India", alertType: "KYC Incomplete", severity: "Low", raisedBy: "Priya Patel", notes: null,
    });
  });

  it("keeps the form open and shows the server error on failure", async () => {
    const { server, user } = await renderApp();
    server.failNext("CreateAlert", "clientName is required");
    await user.click(screen.getByRole("button", { name: "+ Flag client" }));
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Raise alert" }));

    expect(await screen.findByText("clientName is required")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Flag a client" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Raise alert" })).toBeEnabled();
  });

  it("does not add a new row that doesn't match the active filter", async () => {
    const { user } = await renderApp();
    await user.selectOptions(screen.getByLabelText("Filter by alert type"), "AML");
    await waitFor(() => expect(queryTableRow("Maria Garcia")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "+ Flag client" }));
    await fillForm(user); // KYC Incomplete
    await user.click(screen.getByRole("button", { name: "Raise alert" }));

    await waitFor(() => expect(statValue("Total alerts")).toBe("4"));
    expect(queryTableRow("Aisha Khan")).not.toBeInTheDocument();
  });

  it("cancel closes the form without a request", async () => {
    const { server, user } = await renderApp();
    await user.click(screen.getByRole("button", { name: "+ Flag client" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("heading", { name: "Flag a client" })).not.toBeInTheDocument();
    expect(server.ops()).toEqual(["InitialLoad"]);
  });
});

describe("dark mode", () => {
  it("toggles the theme and remembers the choice", async () => {
    const { user } = await renderApp();
    const toggle = screen.getByRole("switch", { name: /dark mode/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(document.documentElement.dataset.theme).toBe("light");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("client-alerts-theme")).toBe("dark");
  });

  it("restores a saved theme on load", async () => {
    localStorage.setItem("client-alerts-theme", "dark");
    await renderApp();
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("switch", { name: /dark mode/i })).toHaveAttribute("aria-checked", "true");
  });
});
