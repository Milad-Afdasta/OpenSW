import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Markdown from "react-markdown";
import {
  Archive,
  BookOpen,
  Check,
  Copy,
  Edit3,
  Eye,
  FileText,
  Link,
  Plus,
  Save,
  Search,
  Undo2,
} from "lucide-react";
import {
  EmptyState,
  ErrorBanner,
  errorText,
  Field,
  formatDate,
  LoadMore,
  Modal,
  query,
  Spinner,
  useAuth,
  useDraftGuard,
  usePaged,
} from "./core";
import type { Note, NoteSummary } from "./types";

export function Notes() {
  const { api, session } = useAuth();
  const { setDirty, guard } = useDraftGuard();
  const [parameters, setParameters] = useSearchParams();
  const routeKey = parameters.toString();
  const currentRoute = useRef(routeKey);
  currentRoute.current = routeKey;
  const selectedId = parameters.get("page")
    ? Number(parameters.get("page"))
    : null;
  const archived = parameters.get("view") === "archive";
  const [search, setSearch] = useState("");
  const list = usePaged<NoteSummary>(
    query("/api/notes", { q: search, archived }),
  );
  const [note, setNote] = useState<Note | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [preview, setPreview] = useState(false);
  const [create, setCreate] = useState(false);
  const [version, setVersion] = useState(0);
  const selectionEpoch = useRef(0);
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  useEffect(() => {
    setCreate(false);
  }, [routeKey]);
  const matching = note !== null && note.id === selectedId;
  const dirty = matching && (title !== note.title || body !== note.body);
  useEffect(() => {
    setDirty(dirty);
  }, [dirty, setDirty]);
  useEffect(() => () => setDirty(false), [setDirty]);
  useEffect(() => {
    const epoch = ++selectionEpoch.current;
    const controller = new AbortController();
    setNote(null);
    setError("");
    setConflict(false);
    setNotice("");
    setLoading(selectedId !== null);
    setPreview(false);
    setSaving(false);
    if (selectedId !== null)
      api<Note>(`/api/notes/${selectedId}`, { signal: controller.signal })
        .then((value) => {
          if (
            epoch === selectionEpoch.current &&
            value.id === selectedRef.current
          ) {
            setNote(value);
            setTitle(value.title);
            setBody(value.body);
            setLoading(false);
          }
        })
        .catch((cause) => {
          if (
            epoch === selectionEpoch.current &&
            !(cause instanceof DOMException && cause.name === "AbortError")
          ) {
            setError(errorText(cause));
            setLoading(false);
          }
        });
    return () => controller.abort();
  }, [api, selectedId, session.user.id, version]);
  const select = (id: number | null, archiveView = archived) => {
    const next: Record<string, string> = {};
    if (id !== null) next.page = String(id);
    if (archiveView) next.view = "archive";
    setParameters(next);
  };
  const save = async () => {
    if (!matching || saving || note.archived) return;
    const current = note;
    const epoch = selectionEpoch.current;
    setSaving(true);
    setError("");
    setConflict(false);
    setNotice("");
    try {
      const saved = await api<Note>(`/api/notes/${current.id}`, {
        method: "PATCH",
        json: { title, body, expected_revision: current.revision },
      });
      if (
        epoch === selectionEpoch.current &&
        saved.id === selectedRef.current
      ) {
        setNote(saved);
        setTitle(saved.title);
        setBody(saved.body);
        setDirty(false);
        list.refresh();
        setNotice("Your page is saved.");
      }
    } catch (cause) {
      if (epoch === selectionEpoch.current) {
        setError(errorText(cause));
        setConflict(
          typeof cause === "object" &&
            cause !== null &&
            "status" in cause &&
            cause.status === 409,
        );
      }
    } finally {
      if (epoch === selectionEpoch.current) setSaving(false);
    }
  };
  const archiveNote = async () => {
    if (!matching || dirty || saving) return;
    const epoch = selectionEpoch.current;
    setSaving(true);
    setError("");
    try {
      const saved = await api<Note>(`/api/notes/${note.id}`, {
        method: "PATCH",
        json: { expected_revision: note.revision, archived: !note.archived },
      });
      if (epoch === selectionEpoch.current) {
        setNote(saved);
        list.refresh();
        select(saved.id, saved.archived);
      }
    } catch (cause) {
      if (epoch === selectionEpoch.current) setError(errorText(cause));
    } finally {
      if (epoch === selectionEpoch.current) setSaving(false);
    }
  };
  return (
    <div className="app-layout notes-layout">
      <aside className="context-sidebar">
        <div className="sidebar-heading">
          <span className="nav-caption">YOUR PAGES</span>
          <button
            className="icon-button"
            aria-label="New page"
            onClick={() => guard(() => setCreate(true))}
          >
            <Plus size={18} />
          </button>
        </div>
        <label className="search-box sidebar-search">
          <Search size={16} />
          <input
            type="search"
            aria-label="Search pages"
            placeholder="Find a page…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <div className="view-switch">
          <button
            className={!archived ? "selected" : ""}
            onClick={() => select(null, false)}
          >
            Pages
          </button>
          <button
            className={archived ? "selected" : ""}
            onClick={() => select(null, true)}
          >
            Archived
          </button>
        </div>
        <ErrorBanner error={list.error} />
        <nav className="page-list" aria-label="Pages">
          {list.items.map((page) => (
            <button
              key={page.id}
              className={`page-list-item ${selectedId === page.id ? "active" : ""}`}
              onClick={() => select(page.id)}
            >
              <FileText size={17} />
              <span>
                {page.title}
                <small>Updated {formatDate(page.updated_at)}</small>
              </span>
            </button>
          ))}
          {!list.loading && !list.items.length && (
            <p className="sidebar-empty">
              {search
                ? "No matching pages."
                : archived
                  ? "No archived pages."
                  : "A fresh page is a good start."}
            </p>
          )}
          <LoadMore
            next={list.next}
            loading={list.loading}
            onClick={() => void list.loadMore()}
          />
        </nav>
        <div className="sidebar-bottom">
          <BookOpen size={18} />
          <div>
            <strong>Space for clear thinking</strong>
            <small>Simple pages. Shared knowledge.</small>
          </div>
        </div>
      </aside>
      <main className="main-panel notes-main">
        {selectedId === null ? (
          <>
            <header className="page-header">
              <div>
                <span className="eyebrow">WRITE IT DOWN. MAKE IT USEFUL.</span>
                <h1>
                  Your team’s knowledge,
                  <br className="desktop-only" /> in one quiet place.
                </h1>
                <p className="muted">
                  Notes, decisions, guides, and the things worth remembering.
                </p>
              </div>
            </header>
            <EmptyState
              title={
                list.items.length
                  ? "Pick up where you left off"
                  : "Start with a blank page"
              }
              description={
                list.items.length
                  ? "Choose a page from the sidebar or make room for a new idea."
                  : "Create a page for your team’s first note, guide, or idea."
              }
              action={
                <button
                  className="button primary"
                  onClick={() => guard(() => setCreate(true))}
                >
                  <Plus size={17} />
                  New page
                </button>
              }
            />
          </>
        ) : loading ? (
          <div className="loading-state">
            <Spinner label="Opening page" />
          </div>
        ) : matching ? (
          <>
            <header className="editor-topbar">
              <div className="editor-breadcrumb">
                <BookOpen size={17} />
                <span>{archived ? "Archived pages" : "Pages"}</span>
              </div>
              <div className="editor-actions">
                <button
                  className="icon-button"
                  aria-label="Copy page link"
                  title="Copy member-only page link"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(location.href);
                      setNotice("Member-only page link copied.");
                    } catch {
                      setError(
                        "Your browser could not copy the link. Copy the address from your browser.",
                      );
                    }
                  }}
                >
                  <Link size={17} />
                </button>
                <button
                  className="button small"
                  disabled={dirty || saving}
                  title={
                    dirty ? "Save your changes before archiving." : undefined
                  }
                  onClick={() => void archiveNote()}
                >
                  {note.archived ? <Undo2 size={16} /> : <Archive size={16} />}
                  {note.archived ? "Restore" : "Archive"}
                </button>
                {!note.archived && (
                  <button
                    className="button primary small"
                    disabled={!dirty || saving || !title.trim()}
                    onClick={() => void save()}
                  >
                    {saving ? <Spinner /> : <Save size={16} />}Save
                  </button>
                )}
              </div>
            </header>
            <div className="note-editor">
              <ErrorBanner error={error} />
              {conflict && (
                <div className="conflict-actions">
                  <button
                    className="button small"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(
                          `${title}\n\n${body}`,
                        );
                        setNotice("Your draft is copied.");
                      } catch {
                        setNotice(
                          "Select your draft text and copy it using your keyboard.",
                        );
                      }
                    }}
                  >
                    <Copy size={15} />
                    Copy draft
                  </button>
                  <button
                    className="button small"
                    onClick={() =>
                      guard(() => {
                        setDirty(false);
                        setVersion((value) => value + 1);
                      })
                    }
                  >
                    Load latest page
                  </button>
                </div>
              )}
              {notice && (
                <div className="notice" role="status">
                  {notice}
                </div>
              )}
              <input
                className="note-title"
                aria-label="Page title"
                value={title}
                maxLength={200}
                disabled={saving || note.archived}
                onChange={(event) => {
                  setTitle(event.target.value);
                  setNotice("");
                }}
                placeholder="Untitled page"
              />
              <div className="note-meta">
                <span className={dirty ? "unsaved" : ""}>
                  {saving ? (
                    <Spinner label="Saving…" />
                  ) : dirty ? (
                    "Unsaved changes"
                  ) : (
                    <>
                      <Check size={14} />
                      Saved {formatDate(note.updated_at)}
                    </>
                  )}
                </span>
                <div className="editor-view-toggle">
                  <button
                    className={!preview ? "selected" : ""}
                    onClick={() => setPreview(false)}
                  >
                    <Edit3 size={14} />
                    Edit
                  </button>
                  <button
                    className={preview ? "selected" : ""}
                    onClick={() => setPreview(true)}
                  >
                    <Eye size={15} />
                    Preview
                  </button>
                </div>
              </div>
              {preview || note.archived ? (
                <article className="markdown-content">
                  <Markdown
                    skipHtml
                    components={{
                      img: () => (
                        <span className="muted">
                          [Image omitted for privacy]
                        </span>
                      ),
                      a: (props) => (
                        <a
                          {...props}
                          target="_blank"
                          rel="noopener noreferrer"
                        />
                      ),
                    }}
                  >
                    {body || "*Nothing written yet.*"}
                  </Markdown>
                </article>
              ) : (
                <textarea
                  className="note-body"
                  aria-label="Page content"
                  placeholder="A clear thought starts here…\n\nWrite in Markdown. Use # for a heading, - for a list, or **bold** for emphasis."
                  value={body}
                  maxLength={200000}
                  disabled={saving}
                  onChange={(event) => {
                    setBody(event.target.value);
                    setNotice("");
                  }}
                />
              )}
              <footer className="editor-footnote">
                <span>Markdown, without the distractions.</span>
                <span>
                  {body.trim() ? body.trim().split(/\s+/).length : 0} words
                </span>
              </footer>
            </div>
          </>
        ) : (
          <div className="page-error">
            <ErrorBanner error={error || "This page is unavailable."} />
            <button className="button" onClick={() => select(null)}>
              Back to pages
            </button>
          </div>
        )}
      </main>
      {create && (
        <Modal
          title="New page"
          onClose={() => {
            if (!saving) setCreate(false);
          }}
        >
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              const owner = currentRoute.current;
              const form = new FormData(event.currentTarget);
              setSaving(true);
              setError("");
              try {
                const created = await api<Note>("/api/notes", {
                  method: "POST",
                  json: { title: form.get("title"), body: "" },
                });
                if (owner === currentRoute.current) {
                  setCreate(false);
                  list.refresh();
                  select(created.id, false);
                }
              } catch (cause) {
                if (owner === currentRoute.current) setError(errorText(cause));
              } finally {
                if (owner === currentRoute.current) setSaving(false);
              }
            }}
          >
            <div className="modal-body">
              <ErrorBanner error={error} />
              <Field label="Page title">
                <input
                  name="title"
                  autoFocus
                  required
                  maxLength={200}
                  placeholder="e.g. How we work"
                  disabled={saving}
                />
              </Field>
            </div>
            <footer className="modal-footer">
              <button
                type="button"
                className="button"
                disabled={saving}
                onClick={() => setCreate(false)}
              >
                Cancel
              </button>
              <button className="button primary" disabled={saving}>
                {saving ? <Spinner /> : "Create page"}
              </button>
            </footer>
          </form>
        </Modal>
      )}
    </div>
  );
}
