import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import App from "./App";
import { TOKEN_KEY } from "./api";
import { PASSWORD, installMockServer, tokenFor } from "./test/mockServer";

afterEach(() => window.history.replaceState(null, "", "/"));

function start({ role } = {}) {
  const server = installMockServer();
  if (role) localStorage.setItem(TOKEN_KEY, tokenFor(role));
  const user = userEvent.setup();
  render(<App />);
  return { server, user };
}

async function startSignedIn(role) {
  const ctx = start({ role });
  await screen.findByText("John Smith");
  return ctx;
}

async function signIn(user, email, password = PASSWORD) {
  await user.type(screen.getByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

const panel = () => screen.getByRole("complementary");
const openAlert = async (user, name) => {
  await user.click(screen.getByText(name, { selector: "td" }));
  return panel();
};

describe("sign in", () => {
  it("shows the sign-in page without calling the API when there is no session", () => {
    const { server } = start();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(server.calls).toHaveLength(0);
  });

  it("signs in, stores the token and loads the dashboard with it", async () => {
    const { server, user } = start();
    await signIn(user, "Analyst@Test.io");

    expect(await screen.findByText("John Smith")).toBeInTheDocument();
    const token = localStorage.getItem(TOKEN_KEY);
    expect(token).toMatch(/^token-session-/);
    expect(server.ops()).toEqual(["SignIn", "InitialLoad"]);
    expect(server.calls[1].token).toBe(token);
    expect(screen.getByText("Andy Analyst")).toBeInTheDocument();
    expect(screen.getByLabelText("Role: Analyst")).toBeInTheDocument();
  });

  it("shows an error for wrong credentials and stays on the page", async () => {
    const { user } = start();
    await signIn(user, "admin@test.io", "nope-nope");
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("restores a stored session with a single request", async () => {
    const { server } = await startSignedIn("Viewer");
    expect(server.ops()).toEqual(["InitialLoad"]);
    expect(screen.getByText("Vic Viewer")).toBeInTheDocument();
  });

  it("returns to sign in with a message when the stored session has expired", async () => {
    installMockServer().expireSessions();
    localStorage.setItem(TOKEN_KEY, "stale-token");
    render(<App />);
    expect(await screen.findByRole("status")).toHaveTextContent("Your session has expired");
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("drops to sign in if the session expires mid-use", async () => {
    const { server, user } = await startSignedIn("Admin");
    server.expireSessions();
    const p = await openAlert(user, "John Smith");
    await user.selectOptions(within(p).getByLabelText("Status"), "Closed");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("sign up", () => {
  it("switches between sign in and sign up and reflects it in the URL", async () => {
    const { user } = start();
    await user.click(screen.getByRole("button", { name: "Create an account" }));
    expect(screen.getByRole("heading", { name: "Create your account" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/signup");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("opens directly on #/signup", () => {
    window.history.replaceState(null, "", "#/signup");
    start();
    expect(screen.getByRole("heading", { name: "Create your account" })).toBeInTheDocument();
  });

  async function fillSignUp(user, { name = "Nia New", email = "nia@test.io", password = "longpassword", confirm = password } = {}) {
    await user.click(screen.getByRole("button", { name: "Create an account" }));
    await user.type(screen.getByLabelText("Full name"), name);
    await user.type(screen.getByLabelText("Email"), email);
    await user.type(screen.getByLabelText("Password"), password);
    await user.type(screen.getByLabelText("Confirm password"), confirm);
    await user.click(screen.getByRole("button", { name: "Create account" }));
  }

  it("creates a Viewer account and signs straight in", async () => {
    const { server, user } = start();
    await fillSignUp(user);
    expect(await screen.findByText("John Smith")).toBeInTheDocument();
    expect(screen.getByText("Nia New")).toBeInTheDocument();
    expect(screen.getByLabelText("Role: Viewer")).toBeInTheDocument();
    expect(server.calls[0].variables).toEqual({ input: { name: "Nia New", email: "nia@test.io", password: "longpassword" } });
    expect(screen.queryByRole("button", { name: "+ Flag client" })).not.toBeInTheDocument();
  });

  it("validates password length and confirmation before calling the API", async () => {
    const { server, user } = start();
    await fillSignUp(user, { password: "short" });
    expect(screen.getByRole("alert")).toHaveTextContent("Password must be at least 8 characters");

    await user.clear(screen.getByLabelText("Password"));
    await user.type(screen.getByLabelText("Password"), "longpassword");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Passwords do not match");
    expect(server.calls).toHaveLength(0);
  });

  it("shows server errors such as a duplicate email", async () => {
    const { user } = start();
    await fillSignUp(user, { email: "admin@test.io" });
    expect(await screen.findByRole("alert")).toHaveTextContent("An account with this email already exists");
  });
});

describe("sign out", () => {
  it("revokes the session server-side and clears it locally", async () => {
    const { server, user } = await startSignedIn("Analyst");
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("You have been signed out.");
    expect(server.calls.at(-1)).toMatchObject({ op: "SignOut", token: tokenFor("Analyst") });
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});

describe("role-based access", () => {
  it("Viewer: read-only — no create, edit, delete or user admin", async () => {
    const { user } = await startSignedIn("Viewer");
    expect(screen.queryByRole("button", { name: "+ Flag client" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();

    const p = await openAlert(user, "John Smith");
    expect(within(p).getByLabelText("Status")).toBeDisabled();
    expect(within(p).getByLabelText("Severity")).toBeDisabled();
    expect(within(p).getByLabelText("Notes")).toHaveAttribute("readonly");
    expect(within(p).queryByRole("button", { name: "Save notes" })).not.toBeInTheDocument();
    expect(within(p).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(within(p).getByText("You have read-only access to alerts.")).toBeInTheDocument();
  });

  it("Analyst: can create and edit, but not delete or manage users", async () => {
    const { user } = await startSignedIn("Analyst");
    expect(screen.getByRole("button", { name: "+ Flag client" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();

    const p = await openAlert(user, "John Smith");
    expect(within(p).getByLabelText("Status")).toBeEnabled();
    expect(within(p).getByRole("button", { name: "Save notes" })).toBeInTheDocument();
    expect(within(p).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("pre-fills 'Raised by' with the signed-in user's name", async () => {
    const { user } = await startSignedIn("Analyst");
    await user.click(screen.getByRole("button", { name: "+ Flag client" }));
    expect(screen.getByLabelText("Raised by")).toHaveValue("Andy Analyst");
  });

  it("Admin: has delete and can change a user's role", async () => {
    const { server, user } = await startSignedIn("Admin");
    const p = await openAlert(user, "John Smith");
    expect(within(p).getByRole("button", { name: "Delete" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Users" }));
    const dialog = await screen.findByRole("dialog", { name: "Users & roles" });
    const roleSelect = await within(dialog).findByLabelText("Role for viewer@test.io");
    await user.selectOptions(roleSelect, "Analyst");

    await waitFor(() => expect(server.calls.at(-1)).toMatchObject({ op: "SetUserRole", variables: { id: 3, role: "Analyst" } }));
    await waitFor(() => expect(within(dialog).getByLabelText("Role for viewer@test.io")).toHaveValue("Analyst"));
  });

  it("Admin: shows an error when trying to remove the last admin", async () => {
    const { user } = await startSignedIn("Admin");
    await user.click(screen.getByRole("button", { name: "Users" }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(await within(dialog).findByLabelText("Role for admin@test.io"), "Viewer");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Cannot remove the last admin");
    expect(within(dialog).getByLabelText("Role for admin@test.io")).toHaveValue("Admin");
  });
});
