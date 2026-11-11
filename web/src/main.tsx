import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  createBrowserRouter,
  RouterProvider,
  useLocation,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  KeyRound,
  LogOut,
  Menu,
  Plus,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";
import {
  AuthProvider,
  Avatar,
  Brand,
  DraftGuardProvider,
  ErrorBanner,
  errorText,
  Field,
  Modal,
  Spinner,
  ThemeButton,
  useAuth,
  useDraftGuard,
  usePaged,
} from "./core";
import { catalog, type AppId, type User } from "./types";
import { Files } from "./Files";
import { Projects } from "./Projects";
import { Notes } from "./Notes";
import { Chat } from "./Chat";
import "./styles.css";

const buildApp = import.meta.env.VITE_OPENSW_APP as AppId;
const application = catalog.apps.find((app) => app.id === buildApp);
if (!application)
  throw new Error(
    "Build this application with VITE_OPENSW_APP=files, projects, notes, or chat.",
  );
document.title = application.name;
document.documentElement.dataset.theme =
  localStorage.getItem("OpenSW-theme") ??
  (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

function Workspace() {
  const { session, signOut } = useAuth();
  const { guard } = useDraftGuard();
  const [parameters, setParameters] = useSearchParams();
  const location = useLocation();
  const [profile, setProfile] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [error, setError] = useState("");
  const [signingOut, setSigningOut] = useState(false);
  const users = usePaged<User>("/api/users");
  useEffect(() => {
    if (users.next !== null && !users.loading && !users.error)
      void users.loadMore();
  }, [users.next, users.loading, users.error]);
  useEffect(() => {
    setProfile(false);
    setMobile(false);
  }, [location.search]);
  if (session.app !== buildApp)
    return (
      <main className="boot-screen">
        <ErrorBanner error="This interface does not match the running app. Ask your administrator to rebuild the selected OpenSW app." />
      </main>
    );
  const view = parameters.get("view");
  return (
    <div className={`workspace app-${buildApp} ${mobile ? "menu-open" : ""}`}>
      <header className="app-bar">
        <div className="app-identity">
          {view !== "teammates" && view !== "password" && (
            <button
              className="icon-button mobile-menu"
              aria-label={mobile ? "Close navigation" : "Open navigation"}
              onClick={() => setMobile((value) => !value)}
            >
              {mobile ? <X size={21} /> : <Menu size={21} />}
            </button>
          )}
          <Brand />
          <span className="brand-divider" />
          <span className="app-name">
            {application!.name.replace("OpenSW ", "")}
          </span>
        </div>
        <div className="app-bar-right">
          <span className="workspace-name">
            <span className="workspace-dot" />
            {session.team_name}
          </span>
          <ThemeButton />
          <div className="profile-menu">
            <button
              className="profile-button"
              aria-label="Open account menu"
              aria-expanded={profile}
              onClick={() => setProfile((value) => !value)}
            >
              <Avatar user={session.user} small />
              <span>{session.user.display_name}</span>
              <ChevronDown size={14} />
            </button>
            {profile && (
              <>
                <button
                  className="menu-dismiss"
                  aria-label="Close account menu"
                  onClick={() => setProfile(false)}
                />
                <div className="dropdown">
                  <div className="dropdown-profile">
                    <strong>{session.user.display_name}</strong>
                    <small>@{session.user.username}</small>
                  </div>
                  {session.user.role === "admin" && (
                    <button
                      onClick={() => {
                        setProfile(false);
                        setParameters({ view: "teammates" });
                      }}
                    >
                      <Users size={16} />
                      Teammates
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setProfile(false);
                      setParameters({ view: "password" });
                    }}
                  >
                    <KeyRound size={16} />
                    Change password
                  </button>
                  <button
                    disabled={signingOut}
                    onClick={() => {
                      setProfile(false);
                      guard(() => {
                        setSigningOut(true);
                        setError("");
                        void signOut()
                          .catch((cause) => setError(errorText(cause)))
                          .finally(() => setSigningOut(false));
                      });
                    }}
                  >
                    <LogOut size={16} />
                    Sign out
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </header>
      {error && <ErrorBanner error={error} />}
      {users.error && (
        <ErrorBanner error={users.error}>
          <button className="text-button" onClick={users.refresh}>
            Reload teammates
          </button>
        </ErrorBanner>
      )}
      {mobile && (
        <button
          className="navigation-scrim"
          aria-label="Close navigation"
          onClick={() => setMobile(false)}
        />
      )}
      {view === "teammates" && session.user.role === "admin" ? (
        <TeamSettings
          users={users.items}
          refresh={users.refresh}
          onBack={() => setParameters({})}
        />
      ) : view === "password" ? (
        <PasswordSettings onBack={() => setParameters({})} />
      ) : buildApp === "files" ? (
        <Files />
      ) : buildApp === "projects" ? (
        <Projects users={users.items} />
      ) : buildApp === "notes" ? (
        <Notes />
      ) : (
        <Chat users={users.items} />
      )}
    </div>
  );
}

function TeamSettings({
  users,
  refresh,
  onBack,
}: {
  users: User[];
  refresh: () => void;
  onBack: () => void;
}) {
  const { api, session } = useAuth();
  const [create, setCreate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [toggle, setToggle] = useState<User | null>(null);
  return (
    <main className="settings-main">
      <button className="text-button back-link" onClick={onBack}>
        <ArrowLeft size={16} />
        Back to {application!.name.replace("OpenSW ", "")}
      </button>
      <header className="page-header">
        <div>
          <span className="eyebrow">A LITTLE TEAMWORK GOES A LONG WAY</span>
          <h1>Good people. Shared space.</h1>
          <p className="muted">Manage the people who can use this workspace.</p>
        </div>
        <button
          className="button primary"
          onClick={() => {
            setError("");
            setCreate(true);
          }}
        >
          <Plus size={17} />
          Add teammate
        </button>
      </header>
      <ErrorBanner error={error} />
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      <section className="content-card">
        <div className="table-scroll">
          <table className="people-table">
            <thead>
              <tr>
                <th>Teammate</th>
                <th className="hide-mobile">Role</th>
                <th>Status</th>
                <th>
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id}>
                  <td>
                    <div className="person-cell">
                      <Avatar user={user} />
                      <div>
                        <strong>
                          {user.display_name}
                          {user.id === session.user.id && (
                            <small className="you-tag">You</small>
                          )}
                        </strong>
                        <small>@{user.username}</small>
                      </div>
                    </div>
                  </td>
                  <td className="hide-mobile">
                    <span className="role-tag">
                      {user.role === "admin" && <ShieldCheck size={13} />}
                      {user.role === "admin" ? "Administrator" : "Member"}
                    </span>
                  </td>
                  <td>
                    <span
                      className={`state-tag ${user.active ? "active" : ""}`}
                    >
                      {user.active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td>
                    {user.role !== "admin" && (
                      <button
                        className="button small subtle"
                        onClick={() => {
                          setError("");
                          setToggle(user);
                        }}
                      >
                        {user.active ? "Deactivate" : "Reactivate"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <p className="content-footnote">
        Accounts belong only to this app. Deactivated teammates keep their
        authorship, but lose access.
      </p>
      {create && (
        <Modal
          title="Add teammate"
          onClose={() => {
            if (!busy) setCreate(false);
          }}
        >
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              setBusy(true);
              setError("");
              try {
                const created = await api<User>("/api/users", {
                  method: "POST",
                  json: {
                    username: form.get("username"),
                    display_name: form.get("display_name"),
                    password: form.get("password"),
                  },
                });
                setCreate(false);
                refresh();
                setNotice(
                  `${created.display_name} can now sign in. Share their credentials privately.`,
                );
              } catch (cause) {
                setError(errorText(cause));
              } finally {
                setBusy(false);
              }
            }}
          >
            <div className="modal-body">
              <ErrorBanner error={error} />
              <Field label="Display name">
                <input
                  name="display_name"
                  autoFocus
                  required
                  maxLength={80}
                  placeholder="e.g. Alex Morgan"
                  disabled={busy}
                />
              </Field>
              <Field
                label="Username"
                hint="3–32 lowercase letters, numbers, dots, underscores, or hyphens."
              >
                <input
                  name="username"
                  required
                  minLength={3}
                  maxLength={32}
                  pattern="[a-z0-9_.\-]+"
                  autoComplete="off"
                  placeholder="e.g. alex.morgan"
                  disabled={busy}
                />
              </Field>
              <Field
                label="Password"
                hint="At least 12 characters. Share it with your teammate privately."
              >
                <input
                  name="password"
                  type="password"
                  required
                  minLength={12}
                  maxLength={256}
                  autoComplete="new-password"
                  disabled={busy}
                />
              </Field>
            </div>
            <footer className="modal-footer">
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() => setCreate(false)}
              >
                Cancel
              </button>
              <button className="button primary" disabled={busy}>
                {busy ? <Spinner /> : "Create account"}
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {toggle && (
        <Modal
          title={
            toggle.active ? "Deactivate teammate?" : "Reactivate teammate?"
          }
          onClose={() => {
            if (!busy) setToggle(null);
          }}
        >
          <div className="modal-body">
            <ErrorBanner error={error} />
            <p>
              {toggle.active
                ? `${toggle.display_name} will be signed out and lose access. Their work will stay in the workspace.`
                : `${toggle.display_name} will be able to sign in again with their existing password.`}
            </p>
          </div>
          <footer className="modal-footer">
            <button
              className="button"
              disabled={busy}
              onClick={() => setToggle(null)}
            >
              Cancel
            </button>
            <button
              className={`button ${toggle.active ? "danger" : "primary"}`}
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  await api(`/api/users/${toggle.id}`, {
                    method: "PATCH",
                    json: { active: !toggle.active },
                  });
                  setToggle(null);
                  refresh();
                } catch (cause) {
                  setError(errorText(cause));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? (
                <Spinner />
              ) : toggle.active ? (
                "Deactivate teammate"
              ) : (
                "Reactivate teammate"
              )}
            </button>
          </footer>
        </Modal>
      )}
    </main>
  );
}

function PasswordSettings({ onBack }: { onBack: () => void }) {
  const { api, forgetSession } = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <main className="settings-main narrow">
      <button className="text-button back-link" onClick={onBack}>
        <ArrowLeft size={16} />
        Back to {application!.name.replace("OpenSW ", "")}
      </button>
      <header className="page-header">
        <div>
          <span className="eyebrow">YOUR ACCOUNT</span>
          <h1>A fresh password.</h1>
          <p className="muted">
            You’ll sign in again on every device after changing it.
          </p>
        </div>
      </header>
      <form
        className="content-card password-form"
        onSubmit={async (event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          if (form.get("new_password") !== form.get("confirm_password")) {
            setError("The new passwords do not match.");
            return;
          }
          setBusy(true);
          setError("");
          try {
            await api("/api/auth/password", {
              method: "POST",
              json: {
                current_password: form.get("current_password"),
                new_password: form.get("new_password"),
              },
            });
            forgetSession();
          } catch (cause) {
            setError(errorText(cause));
          } finally {
            setBusy(false);
          }
        }}
      >
        <ErrorBanner error={error} />
        <Field label="Current password">
          <input
            name="current_password"
            type="password"
            autoComplete="current-password"
            required
            disabled={busy}
          />
        </Field>
        <Field label="New password" hint="Use at least 12 characters.">
          <input
            name="new_password"
            type="password"
            autoComplete="new-password"
            minLength={12}
            maxLength={256}
            required
            disabled={busy}
          />
        </Field>
        <Field label="Confirm new password">
          <input
            name="confirm_password"
            type="password"
            autoComplete="new-password"
            minLength={12}
            maxLength={256}
            required
            disabled={busy}
          />
        </Field>
        <button className="button primary" disabled={busy}>
          {busy ? <Spinner /> : <Check size={16} />}Change password
        </button>
      </form>
    </main>
  );
}

const router = createBrowserRouter([
  {
    path: "*",
    element: (
      <AuthProvider appName={application.name}>
        <DraftGuardProvider>
          <Workspace />
        </DraftGuardProvider>
      </AuthProvider>
    ),
  },
]);
createRoot(document.getElementById("root")!).render(
  <RouterProvider router={router} />,
);
