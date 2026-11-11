import React, {
  Children,
  cloneElement,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  Check,
  ChevronDown,
  LoaderCircle,
  Moon,
  Sun,
  X,
  AlertCircle,
  Inbox,
} from "lucide-react";
import { useBeforeUnload, useBlocker } from "react-router-dom";
import type { Page, Session, User } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
  ) {
    super(message);
  }
}
type RequestOptions = RequestInit & { json?: unknown };
type Api = <T>(path: string, options?: RequestOptions) => Promise<T>;
const AuthContext = createContext<{
  session: Session;
  api: Api;
  signOut: () => Promise<void>;
  forgetSession: () => void;
  suspended: boolean;
} | null>(null);
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("Authentication context missing");
  return value;
}

export function AuthProvider({
  children,
  appName,
}: {
  children: React.ReactNode;
  appName: string;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const sessionRef = useRef<Session | null>(null);
  const authGeneration = useRef(0);
  const [ready, setReady] = useState(false);
  const [expired, setExpired] = useState(false);
  const [sessionEpoch, setSessionEpoch] = useState(0);
  const initialError = useRef("");
  const api = useCallback<Api>(
    async <T,>(path: string, options: RequestOptions = {}): Promise<T> => {
      const requestGeneration = authGeneration.current;
      const headers = new Headers(options.headers);
      if (options.json !== undefined)
        headers.set("Content-Type", "application/json");
      const method = options.method ?? "GET";
      if (!["GET", "HEAD"].includes(method) && sessionRef.current)
        headers.set("X-CSRF-Token", sessionRef.current.csrf_token);
      let response: Response;
      try {
        response = await fetch(path, {
          ...options,
          headers,
          credentials: "same-origin",
          body:
            options.json !== undefined
              ? JSON.stringify(options.json)
              : options.body,
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError")
          throw error;
        throw new ApiError(
          method === "GET"
            ? "Cannot reach the server. Check your connection and try again."
            : "The connection was interrupted. The result may be unconfirmed. Refresh before trying again.",
        );
      }
      if (!response.ok) {
        let detail = "Something went wrong. Please try again.";
        try {
          const result = (await response.json()) as { detail?: unknown };
          if (typeof result.detail === "string") detail = result.detail;
        } catch {
          /* Preserve useful HTTP status without exposing an HTML response. */
        }
        if (
          response.status === 401 &&
          sessionRef.current &&
          requestGeneration === authGeneration.current &&
          path !== "/api/auth/login"
        )
          setExpired(true);
        throw new ApiError(detail, response.status);
      }
      if (response.status === 204) return undefined as T;
      try {
        return (await response.json()) as T;
      } catch {
        throw new ApiError(
          method === "GET"
            ? "The server returned an unreadable response."
            : "The server response was unreadable. Refresh to check whether your change was saved.",
        );
      }
    },
    [],
  );
  useEffect(() => {
    let current = true;
    api<Session>("/api/me")
      .then((value) => {
        if (current) {
          sessionRef.current = value;
          setSession(value);
        }
      })
      .catch((error) => {
        if (error instanceof ApiError && error.status !== 401)
          initialError.current = error.message;
      })
      .finally(() => {
        if (current) setReady(true);
      });
    return () => {
      current = false;
    };
  }, [api]);
  const acceptSession = (value: Session) => {
    authGeneration.current++;
    if (sessionRef.current && sessionRef.current.user.id !== value.user.id)
      setSessionEpoch((epoch) => epoch + 1);
    sessionRef.current = value;
    setSession(value);
    setExpired(false);
  };
  const forgetSession = () => {
    authGeneration.current++;
    sessionRef.current = null;
    setSession(null);
    setExpired(false);
    setSessionEpoch((epoch) => epoch + 1);
  };
  const signOut = async () => {
    await api("/api/auth/logout", { method: "POST" });
    forgetSession();
  };
  if (!ready)
    return (
      <div className="boot-screen">
        <Spinner label="Opening your workspace" />
      </div>
    );
  if (!session)
    return (
      <SignIn
        appName={appName}
        api={api}
        onSuccess={acceptSession}
        initialError={initialError.current}
      />
    );
  return (
    <AuthContext.Provider
      value={{ session, api, signOut, forgetSession, suspended: expired }}
    >
      <div
        key={`${session.app}:${session.user.id}:${sessionEpoch}`}
        inert={expired}
      >
        {children}
      </div>
      {expired && (
        <Reauthenticate appName={appName} api={api} onSuccess={acceptSession} />
      )}
    </AuthContext.Provider>
  );
}

function Reauthenticate({
  appName,
  api,
  onSuccess,
}: {
  appName: string;
  api: Api;
  onSuccess: (session: Session) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="session-dialog"
      aria-label="Sign in to continue"
      onCancel={(event) => event.preventDefault()}
    >
      <SignIn appName={appName} api={api} onSuccess={onSuccess} expired />
    </dialog>
  );
}

function SignIn({
  appName,
  api,
  onSuccess,
  expired = false,
  initialError = "",
}: {
  appName: string;
  api: Api;
  onSuccess: (session: Session) => void;
  expired?: boolean;
  initialError?: string;
}) {
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);
  return (
    <main className="login-page">
      <div className="login-decoration">
        <div className="ambient-orb" />
        <div className="login-story">
          <Brand />
          <h1>
            Your work.
            <br />
            Your space.
          </h1>
          <p>
            A little less software.
            <br />A lot more room to focus.
          </p>
          <span className="ownership-tag">
            <Check size={15} /> Hosted by you. Controlled by you.
          </span>
        </div>
      </div>
      <section className="login-form-wrap">
        <form
          className="login-form"
          onSubmit={async (event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            setBusy(true);
            setError("");
            try {
              onSuccess(
                await api<Session>("/api/auth/login", {
                  method: "POST",
                  json: {
                    username: form.get("username"),
                    password: form.get("password"),
                  },
                }),
              );
            } catch (cause) {
              setError(errorText(cause));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="mobile-login-brand">
            <Brand />
          </div>
          <span className="eyebrow">{appName}</span>
          <h2>{expired ? "Welcome back" : "Make yourself at home"}</h2>
          <p className="muted">
            {expired
              ? "Your session ended. Sign in with the same account to continue your unsaved work."
              : "Sign in to your team’s private workspace."}
          </p>
          <ErrorBanner error={error} />
          <Field label="Username">
            <input
              name="username"
              autoComplete="username"
              autoFocus
              required
              disabled={busy}
              placeholder="Your username"
            />
          </Field>
          <Field label="Password">
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
              disabled={busy}
              placeholder="Your password"
            />
          </Field>
          <button className="button primary full" disabled={busy}>
            {busy ? <Spinner /> : "Sign in"}{" "}
          </button>
          <p className="login-footnote">
            Need an account? Ask your workspace administrator.
          </p>
        </form>
      </section>
    </main>
  );
}

export function Brand() {
  return (
    <div className="brand">
      <img src="/OpenSW-logo.png" alt="OpenSW" />
    </div>
  );
}
export function errorText(cause: unknown): string {
  return cause instanceof Error
    ? cause.message
    : "Something went wrong. Please try again.";
}
export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner" role="status">
      <LoaderCircle size={18} className="spin" />
      {label && <span>{label}</span>}
    </span>
  );
}
export function ErrorBanner({
  error,
  children,
}: {
  error?: string | null;
  children?: React.ReactNode;
}) {
  return error ? (
    <div className="error-banner" role="alert">
      <AlertCircle size={17} />
      <div>
        {error}
        {children}
      </div>
    </div>
  ) : null;
}
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Inbox size={28} />
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  const generated = useId();
  const control = Children.only(children) as React.ReactElement<{
    id?: string;
    "aria-describedby"?: string;
  }>;
  const id = control.props.id ?? generated;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {cloneElement(control, {
        id,
        "aria-describedby": hint
          ? `${id}-hint`
          : control.props["aria-describedby"],
      })}
      {hint && <small id={`${id}-hint`}>{hint}</small>}
    </div>
  );
}
export function Avatar({
  user,
  small = false,
}: {
  user?: Pick<User, "display_name" | "username">;
  small?: boolean;
}) {
  const text = user?.display_name ?? user?.username ?? "?";
  return (
    <span className={`avatar ${small ? "small" : ""}`} title={text}>
      {text
        .split(/\s+/)
        .map((part) => part[0])
        .slice(0, 2)
        .join("")
        .toUpperCase()}
    </span>
  );
}
export function ThemeButton() {
  const [theme, setTheme] = useState(
    () =>
      localStorage.getItem("OpenSW-theme") ??
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("OpenSW-theme", theme);
  }, [theme]);
  return (
    <button
      className="icon-button"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? <Sun size={19} /> : <Moon size={19} />}
    </button>
  );
}
export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}
export function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}
export function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}
export function query(
  path: string,
  values: Record<string, string | number | boolean | null | undefined>,
) {
  const parameters = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "")
      parameters.set(key, String(value));
  });
  return `${path}${parameters.size ? `?${parameters}` : ""}`;
}

export function useResource<T extends { id: number }>(
  base: string,
  id: number | null,
) {
  const { api, session, suspended } = useAuth();
  const valid = id !== null && Number.isSafeInteger(id) && id > 0;
  const path = valid ? `${base}/${id}` : null;
  const key = `${session.app}:${session.user.id}:${path}`;
  const currentKey = useRef(key);
  currentKey.current = key;
  const epoch = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<{
    key: string;
    item: T | null;
    loading: boolean;
    error: string;
  }>({ key: "", item: null, loading: false, error: "" });
  useEffect(() => {
    const requestEpoch = ++epoch.current;
    const controller = new AbortController();
    pending.current = controller;
    if (!path || suspended) {
      setState((previous) => ({
        key,
        item: previous.key === key ? previous.item : null,
        loading: false,
        error: "",
      }));
    } else {
      setState((previous) => ({
        key,
        item: previous.key === key ? previous.item : null,
        loading: true,
        error: "",
      }));
      void api<T>(path, { signal: controller.signal })
        .then((item) => {
          if (item.id !== id)
            throw new ApiError(
              "The server returned a different resource. Refresh before continuing.",
            );
          if (requestEpoch === epoch.current && currentKey.current === key)
            setState({ key, item, loading: false, error: "" });
        })
        .catch((cause) => {
          if (
            requestEpoch === epoch.current &&
            currentKey.current === key &&
            !(cause instanceof DOMException && cause.name === "AbortError")
          )
            setState({
              key,
              item: null,
              loading: false,
              error: errorText(cause),
            });
        });
    }
    return () => {
      controller.abort();
      epoch.current++;
    };
  }, [api, path, id, key, suspended, version]);
  const confirm = (item: T) => {
    if (currentKey.current !== key) return;
    if (item.id !== id)
      throw new ApiError(
        "The server returned a different resource. Refresh before continuing.",
      );
    pending.current?.abort();
    epoch.current++;
    setState({ key, item, loading: false, error: "" });
  };
  const current = state.key === key;
  return {
    item: current ? state.item : null,
    loading: valid && (!current || state.loading),
    error:
      id !== null && !valid
        ? "This link has an invalid resource ID."
        : current
          ? state.error
          : "",
    refresh: () => setVersion((value) => value + 1),
    confirm,
  };
}

export function usePaged<T extends { id: number }>(path: string | null) {
  const { api } = useAuth();
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<{
    path: string | null;
    items: T[];
    next: number | null;
    loading: boolean;
    error: string;
    extra: Record<string, unknown>;
  }>({
    path: null,
    items: [],
    next: null,
    loading: false,
    error: "",
    extra: {},
  });
  const epoch = useRef(0);
  const activePath = useRef(path);
  activePath.current = path;
  useEffect(() => {
    const id = ++epoch.current;
    if (!path) {
      setState({
        path,
        items: [],
        next: null,
        loading: false,
        error: "",
        extra: {},
      });
      return;
    }
    const controller = new AbortController();
    setState((previous) => ({
      path,
      items: previous.path === path ? previous.items : [],
      next: null,
      loading: true,
      error: "",
      extra: {},
    }));
    api<Page<T> & Record<string, unknown>>(path, { signal: controller.signal })
      .then((page) => {
        if (id === epoch.current)
          setState({
            path,
            items: page.items,
            next: page.next_cursor,
            loading: false,
            error: "",
            extra: page,
          });
      })
      .catch((error) => {
        if (
          id === epoch.current &&
          !(error instanceof DOMException && error.name === "AbortError")
        )
          setState((previous) => ({
            ...previous,
            loading: false,
            error: errorText(error),
          }));
      });
    return () => controller.abort();
  }, [api, path, version]);
  const loadMore = async () => {
    if (!path || state.next === null || state.loading) return;
    const id = epoch.current;
    setState((previous) => ({ ...previous, loading: true, error: "" }));
    try {
      const page = await api<Page<T>>(
        `${path}${path.includes("?") ? "&" : "?"}cursor=${state.next}`,
      );
      if (id === epoch.current && path === activePath.current)
        setState((previous) => ({
          ...previous,
          items: [...previous.items, ...page.items],
          next: page.next_cursor,
          loading: false,
        }));
    } catch (cause) {
      if (id === epoch.current)
        setState((previous) => ({
          ...previous,
          loading: false,
          error: errorText(cause),
        }));
    }
  };
  const current = state.path === path;
  return {
    items: current ? state.items : [],
    next: current ? state.next : null,
    loading: !current || state.loading,
    error: current ? state.error : "",
    extra: current ? state.extra : {},
    refresh: () => setVersion((value) => value + 1),
    loadMore,
  };
}
export function LoadMore({
  next,
  loading,
  onClick,
}: {
  next: number | null;
  loading: boolean;
  onClick: () => void;
}) {
  return next !== null ? (
    <button
      className="button subtle load-more"
      disabled={loading}
      onClick={onClick}
    >
      {loading ? <Spinner /> : <ChevronDown size={16} />}Load more
    </button>
  ) : null;
}

export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => {
      element?.close();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className={`modal ${wide ? "wide" : ""}`}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
    >
      <header className="modal-header">
        <h2>{title}</h2>
        <button
          className="icon-button"
          aria-label={`Close ${title}`}
          onClick={onClose}
        >
          <X size={19} />
        </button>
      </header>
      {children}
    </dialog>
  );
}
type Guard = {
  dirty: boolean;
  setDirty: (value: boolean) => void;
  guard: (action: () => void) => void;
};
const GuardContext = createContext<Guard | null>(null);
export function useDraftGuard() {
  const guard = useContext(GuardContext);
  if (!guard) throw new Error("Draft guard missing");
  return guard;
}
export function DraftGuardProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const blocker = useBlocker(dirty);
  useBeforeUnload(
    useCallback(
      (event) => {
        if (dirty) {
          event.preventDefault();
          event.returnValue = "";
        }
      },
      [dirty],
    ),
  );
  return (
    <GuardContext.Provider
      value={{
        dirty,
        setDirty,
        guard: (action) => {
          if (dirty) setPending(() => action);
          else action();
        },
      }}
    >
      {children}
      {(pending || blocker.state === "blocked") && (
        <Modal
          title="Keep your changes?"
          onClose={() => {
            setPending(null);
            if (blocker.state === "blocked") blocker.reset();
          }}
        >
          <div className="modal-body">
            <p>
              You have unsaved changes. Keep editing, or discard them before
              leaving.
            </p>
          </div>
          <footer className="modal-footer">
            <button
              className="button"
              onClick={() => {
                setPending(null);
                if (blocker.state === "blocked") blocker.reset();
              }}
            >
              Keep editing
            </button>
            <button
              className="button danger"
              onClick={() => {
                setDirty(false);
                if (blocker.state === "blocked") blocker.proceed();
                const action = pending;
                setPending(null);
                action?.();
              }}
            >
              Discard changes
            </button>
          </footer>
        </Modal>
      )}
    </GuardContext.Provider>
  );
}
