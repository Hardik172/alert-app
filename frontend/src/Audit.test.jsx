import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import { TOKEN_KEY } from "./api";
import { PAGE_SIZE } from "./AuditLog";
import { installMockServer, tokenFor } from "./test/mockServer";

// Pre-existing history for alert #1 (newest first, like the server).
const HISTORY = [
  {
    id: 2, at: "2026-11-08T10:30:00", actorId: 2, actorName: "Andy Analyst", action: "alert.update",
    entityType: "alert", entityId: 1, changes: [{ field: "severity", from: "Medium", to: "High" }], snapshot: null,
  },
  {
    id: 1, at: "2026-11-08T09:00:00", actorId: 1, actorName: "Ada Admin", action: "alert.create",
    entityType: "alert", entityId: 1, changes: [{ field: "client_name", from: null, to: "John Smith" }], snapshot: null,
  },
];

async function startAs(role, serverOptions = { audit: HISTORY }) {
  const server = installMockServer(serverOptions);
  localStorage.setItem(TOKEN_KEY, tokenFor(role));
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText("John Smith");
  return { server, user };
}

async function openHistory(user, name) {
  await user.click(screen.getByText(name, { selector: "td" }));
  const panel = screen.getByRole("complementary");
  await user.click(within(panel).getByRole("button", { name: /History/ }));
  return panel;
}

const timelineItems = (panel) => within(panel).queryAllByRole("listitem").map((li) => li.textContent);

describe("alert history timeline", () => {
  it("is collapsed by default and loads with one request when expanded", async () => {
    const { server, user } = await startAs("Viewer");
    await user.click(screen.getByText("John Smith", { selector: "td" }));
    expect(server.ops()).toEqual(["InitialLoad"]); // opening the panel costs nothing

    const panel = await openHistory(user, "John Smith");
    await waitFor(() => expect(timelineItems(panel)).toHaveLength(2));
    const [latest, created] = timelineItems(panel);
    expect(latest).toMatch(/Andy Analyst changed severity/);
    expect(latest).toMatch(/severity: Medium → High/);
    expect(created).toMatch(/Ada Admin raised this alert/);
    expect(server.ops()).toEqual(["InitialLoad", "AlertHistory"]);
    expect(server.calls[1].variables).toEqual({ id: 1 });
  });

  it("shows an empty state for alerts with no recorded changes", async () => {
    const { user } = await startAs("Viewer");
    const panel = await openHistory(user, "Maria Garcia");
    expect(await within(panel).findByText("No recorded changes yet.")).toBeInTheDocument();
  });

  it("adds an update to the timeline without refetching", async () => {
    const { server, user } = await startAs("Analyst");
    const panel = await openHistory(user, "John Smith");
    await waitFor(() => expect(timelineItems(panel)).toHaveLength(2));

    await user.selectOptions(within(panel).getByLabelText("Status"), "Closed");
    await waitFor(() => expect(timelineItems(panel)).toHaveLength(3));
    expect(timelineItems(panel)[0]).toMatch(/Andy Analyst changed status.*status: Open → Closed/);
    expect(server.ops()).toEqual(["InitialLoad", "AlertHistory", "UpdateAlert"]);
  });

  it("starts a new alert's history from the create response", async () => {
    const { server, user } = await startAs("Analyst", {});
    await user.click(screen.getByRole("button", { name: "+ Flag client" }));
    for (const [label, value] of [["Client name", "Nora New"], ["Address", "1 Road"], ["Country", "Peru"]]) {
      await user.type(screen.getByLabelText(label), value);
    }
    screen.getByLabelText("Date of birth").value = "";
    await user.type(screen.getByLabelText("Date of birth"), "1990-01-01");
    await user.click(screen.getByRole("button", { name: "Raise alert" }));

    const panel = await screen.findByRole("complementary");
    await within(panel).findByRole("heading", { name: "Nora New" });
    await user.click(within(panel).getByRole("button", { name: /History/ }));
    expect(timelineItems(panel)).toEqual([expect.stringMatching(/Andy Analyst raised this alert/)]);
    expect(server.ops()).not.toContain("AlertHistory");
  });
});

describe("audit log screen", () => {
  it("is available to Auditors and Admins only", async () => {
    await startAs("Auditor");
    expect(screen.getByRole("button", { name: "Audit log" })).toBeInTheDocument();
    // Auditors can't change anything
    expect(screen.queryByRole("button", { name: "+ Flag client" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Role: Auditor")).toBeInTheDocument();
  });

  it.each(["Viewer", "Analyst"])("is hidden from %s", async (role) => {
    await startAs(role);
    expect(screen.queryByRole("button", { name: "Audit log" })).not.toBeInTheDocument();
  });

  it("lists entries with readable actions and targets", async () => {
    const { server, user } = await startAs("Auditor");
    await user.click(screen.getByRole("button", { name: "Audit log" }));
    const dialog = await screen.findByRole("dialog", { name: "Audit log" });
    await within(dialog).findByText("Andy Analyst");

    const rows = within(dialog).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Alert updated")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Alert #1")).toBeInTheDocument();
    expect(within(rows[0]).getByText("severity: Medium → High")).toBeInTheDocument();
    expect(within(dialog).getByText("Showing 1–2 of 2")).toBeInTheDocument();
    expect(server.calls.at(-1)).toMatchObject({ op: "AuditLog", variables: { filter: {}, limit: PAGE_SIZE, offset: 0 } });
  });

  it("filters by action and user", async () => {
    const { server, user } = await startAs("Admin");
    await user.click(screen.getByRole("button", { name: "Audit log" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Andy Analyst");

    await user.selectOptions(within(dialog).getByLabelText("Filter by action"), "alert.create");
    await waitFor(() => expect(within(dialog).queryByText("Andy Analyst")).not.toBeInTheDocument());
    expect(within(dialog).getByText("Ada Admin")).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Filter by user"), "nobody");
    expect(await within(dialog).findByText("No audit entries match these filters.")).toBeInTheDocument();
    expect(server.calls.at(-1).variables.filter).toEqual({ action: "alert.create", actor: "nobody" });
  });

  it("shows the snapshot of a deleted alert", async () => {
    const { user } = await startAs("Admin");
    await user.click(screen.getByText("Carlos Silva", { selector: "td" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(screen.queryByRole("complementary")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Audit log" }));
    const dialog = await screen.findByRole("dialog");
    const deleted = await within(dialog).findByText("Alert deleted", { selector: ".badge" });
    const row = deleted.closest("tr");
    await user.click(within(row).getByText("Deleted record"));
    expect(within(row).getByText("Av. Paulista 1000, São Paulo")).toBeInTheDocument();
  });

  it("pages through results", async () => {
    const many = Array.from({ length: PAGE_SIZE + 3 }, (_, i) => ({
      ...HISTORY[0], id: 100 - i, changes: [{ field: "notes", from: null, to: `note ${i}` }],
    }));
    const { server, user } = await startAs("Auditor", { audit: many });
    await user.click(screen.getByRole("button", { name: "Audit log" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(`Showing 1–${PAGE_SIZE} of ${PAGE_SIZE + 3}`)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Previous" })).toBeDisabled();

    await user.click(within(dialog).getByRole("button", { name: "Next" }));
    expect(await within(dialog).findByText(`Showing ${PAGE_SIZE + 1}–${PAGE_SIZE + 3} of ${PAGE_SIZE + 3}`)).toBeInTheDocument();
    expect(server.calls.at(-1).variables).toMatchObject({ offset: PAGE_SIZE });
    expect(within(dialog).getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("exports the filtered log as CSV with the session token", async () => {
    const createObjectURL = vi.fn(() => "blob:audit");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    const { server, user } = await startAs("Auditor");
    await user.click(screen.getByRole("button", { name: "Audit log" }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText("Filter by action"), "alert.update");
    await user.click(within(dialog).getByRole("button", { name: "Export CSV" }));

    await waitFor(() => expect(click).toHaveBeenCalled());
    const exportCall = server.calls.find((c) => c.op === "AuditCsv");
    expect(exportCall.url).toBe("/api/audit.csv?action=alert.update");
    expect(exportCall.token).toBe(tokenFor("Auditor"));
    const blob = createObjectURL.mock.calls[0][0];
    const text = await new Promise((resolve) => {
      const reader = new FileReader(); // jsdom's Blob has no .text()
      reader.onload = () => resolve(reader.result);
      reader.readAsText(blob);
    });
    expect(text).toMatch(/^id,at,actor_name,action\n2,.*Andy Analyst,alert\.update$/);
  });
});
