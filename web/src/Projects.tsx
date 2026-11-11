import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Archive,
  CalendarDays,
  Circle,
  FolderKanban,
  MessageSquare,
  Plus,
  Search,
  Undo2,
} from "lucide-react";
import {
  ApiError,
  Avatar,
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
  useResource,
} from "./core";
import {
  catalog,
  type Comment,
  type Project,
  type Task,
  type TaskSummary,
  type User,
} from "./types";

export function Projects({ users }: { users: User[] }) {
  const { api } = useAuth();
  const { guard } = useDraftGuard();
  const [parameters, setParameters] = useSearchParams();
  const routeKey = parameters.toString();
  const currentRoute = useRef(routeKey);
  currentRoute.current = routeKey;
  const projectId = parameters.get("project")
    ? Number(parameters.get("project"))
    : null;
  const taskId = parameters.get("task") ? Number(parameters.get("task")) : null;
  const archived = parameters.get("view") === "archive";
  const archivedTasks = parameters.get("tasks") === "archive";
  const projects = usePaged<Project>(query("/api/projects", { archived }));
  const projectDetail = useResource<Project>("/api/projects", projectId);
  const selected = projectDetail.item;
  const [search, setSearch] = useState("");
  const [assignee, setAssignee] = useState("");
  const tasks = usePaged<TaskSummary>(
    selected
      ? query(`/api/projects/${projectId}/tasks`, {
          archived: archivedTasks,
          q: search,
          assignee_id: assignee,
        })
      : null,
  );
  const [modal, setModal] = useState<
    "project" | "task" | "archive-project" | null
  >(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setModal(null);
    setBusy(false);
    setError("");
  }, [routeKey]);
  const navigate = (
    nextProject: number | null,
    nextTask: number | null = null,
    archiveView = archived,
    archiveTaskView = archivedTasks,
  ) => {
    const next: Record<string, string> = {};
    if (nextProject !== null) next.project = String(nextProject);
    if (nextTask !== null) next.task = String(nextTask);
    if (archiveView) next.view = "archive";
    if (archiveTaskView) next.tasks = "archive";
    setParameters(next);
  };
  return (
    <div className="app-layout">
      <aside className="context-sidebar">
        <div className="sidebar-heading">
          <span className="nav-caption">PROJECTS</span>
          <button
            className="icon-button"
            aria-label="New project"
            onClick={() =>
              guard(() => {
                setError("");
                setModal("project");
              })
            }
          >
            <Plus size={18} />
          </button>
        </div>
        <div className="view-switch">
          <button
            className={!archived ? "selected" : ""}
            onClick={() => navigate(null, null, false, false)}
          >
            Active
          </button>
          <button
            className={archived ? "selected" : ""}
            onClick={() => navigate(null, null, true, false)}
          >
            Archived
          </button>
        </div>
        <nav className="page-list" aria-label="Projects">
          {projects.items.map((project) => (
            <button
              key={project.id}
              className={`page-list-item ${projectId === project.id ? "active" : ""}`}
              onClick={() => {
                setSearch("");
                setAssignee("");
                navigate(project.id, null, archived, false);
              }}
            >
              <FolderKanban size={18} />
              <span>{project.name}</span>
            </button>
          ))}
          <ErrorBanner error={projects.error} />
          <LoadMore
            next={projects.next}
            loading={projects.loading}
            onClick={() => void projects.loadMore()}
          />
          {!projects.loading && !projects.items.length && (
            <p className="sidebar-empty">
              {archived
                ? "No archived projects."
                : "Your next great project starts here."}
            </p>
          )}
        </nav>
        <div className="sidebar-bottom">
          <FolderKanban size={18} />
          <div>
            <strong>Keep work moving</strong>
            <small>One clear step at a time.</small>
          </div>
        </div>
      </aside>
      <main className="main-panel project-main">
        <header className="page-header">
          <div>
            <span className="eyebrow">LESS PROCESS. MORE PROGRESS.</span>
            <h1>
              {selected?.name ??
                (projectId !== null
                  ? "Opening project…"
                  : "Make room for progress.")}
            </h1>
            <p className="muted">
              {selected?.description ||
                "A clear view of what’s next, what’s moving, and what’s done."}
            </p>
          </div>
          <div className="header-actions">
            {selected && (
              <button
                className="button"
                onClick={() =>
                  guard(() => {
                    setError("");
                    setModal("archive-project");
                  })
                }
              >
                {selected.archived ? (
                  <Undo2 size={16} />
                ) : (
                  <Archive size={16} />
                )}
                {selected.archived ? "Restore project" : "Archive project"}
              </button>
            )}
            {selected && !selected.archived && !archived && (
              <button
                className="button primary"
                onClick={() =>
                  guard(() => {
                    setError("");
                    setModal("task");
                  })
                }
              >
                <Plus size={17} />
                New task
              </button>
            )}
            {!projectId && (
              <button
                className="button primary"
                onClick={() => setModal("project")}
              >
                <Plus size={17} />
                New project
              </button>
            )}
          </div>
        </header>
        <ErrorBanner error={error || projectDetail.error || tasks.error}>
          {projectDetail.error && (
            <button className="text-button" onClick={projectDetail.refresh}>
              Reload project
            </button>
          )}
        </ErrorBanner>
        {!projectId ? (
          <EmptyState
            title="Good work starts with a simple plan"
            description="Create a project, add a few tasks, and take the next step together."
            action={
              <button
                className="button primary"
                onClick={() => setModal("project")}
              >
                <Plus size={17} />
                New project
              </button>
            }
          />
        ) : !selected ? (
          <div className="loading-state">
            {projectDetail.loading && <Spinner label="Loading project" />}
          </div>
        ) : (
          <>
            <div className="toolbar">
              <div className="view-switch compact">
                <button
                  className={!archivedTasks ? "selected" : ""}
                  onClick={() => navigate(projectId, null, archived, false)}
                >
                  Board
                </button>
                <button
                  className={archivedTasks ? "selected" : ""}
                  onClick={() => navigate(projectId, null, archived, true)}
                >
                  Archived tasks
                </button>
              </div>
              <div className="filter-group">
                <label className="search-box">
                  <Search size={17} />
                  <input
                    type="search"
                    placeholder="Search tasks"
                    aria-label="Search tasks"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </label>
                <select
                  aria-label="Filter by assignee"
                  value={assignee}
                  onChange={(event) => setAssignee(event.target.value)}
                >
                  <option value="">Everyone</option>
                  {users.map((user) => (
                    <option value={user.id} key={user.id}>
                      {user.display_name}
                      {!user.active ? " (inactive)" : ""}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {tasks.loading && !tasks.items.length ? (
              <div className="loading-state">
                <Spinner label="Loading tasks" />
              </div>
            ) : (
              <div className="board">
                {catalog.task_statuses.map((status) => {
                  const column = tasks.items.filter(
                    (task) => task.status === status.id,
                  );
                  return (
                    <section
                      className={`board-column status-${status.id}`}
                      key={status.id}
                    >
                      <header className="column-header">
                        <span className="status-dot" />
                        <h2>{status.label}</h2>
                        <span className="count-badge">{column.length}</span>
                      </header>
                      <div className="task-stack">
                        {column.map((task) => (
                          <button
                            key={task.id}
                            className="task-card"
                            onClick={() => navigate(projectId, task.id)}
                          >
                            <div className="task-card-top">
                              <span className="task-id">TASK-{task.id}</span>
                              {task.priority !== "normal" && (
                                <span className={`priority ${task.priority}`}>
                                  {
                                    catalog.task_priorities.find(
                                      (priority) =>
                                        priority.id === task.priority,
                                    )?.label
                                  }
                                </span>
                              )}
                            </div>
                            <h3>{task.title}</h3>
                            <footer>
                              {task.due_date ? (
                                <span className="due-date">
                                  <CalendarDays size={13} />
                                  {task.due_date}
                                </span>
                              ) : (
                                <span className="muted">No due date</span>
                              )}
                              {task.assignee_id ? (
                                <Avatar
                                  user={users.find(
                                    (user) => user.id === task.assignee_id,
                                  )}
                                  small
                                />
                              ) : (
                                <span
                                  className="unassigned-avatar"
                                  title="Unassigned"
                                >
                                  —
                                </span>
                              )}
                            </footer>
                          </button>
                        ))}
                        {!column.length && (
                          <div className="empty-column">
                            <Circle size={18} />
                            <span>
                              {search || assignee
                                ? "No matching tasks"
                                : "Nothing here yet"}
                            </span>
                          </div>
                        )}
                      </div>
                    </section>
                  );
                })}
              </div>
            )}
            <LoadMore
              next={tasks.next}
              loading={tasks.loading}
              onClick={() => void tasks.loadMore()}
            />
            {tasks.next !== null && (
              <p className="content-footnote">
                Showing loaded tasks. Load more to see the next page.
              </p>
            )}
          </>
        )}
      </main>
      {taskId !== null && selected && (
        <TaskDetail
          key={`${selected.id}:${taskId}`}
          id={taskId}
          projectId={selected.id}
          users={users}
          readOnly={selected.archived}
          onClose={() => navigate(projectId)}
          onSaved={tasks.refresh}
        />
      )}
      {modal && (
        <Modal
          title={
            modal === "project"
              ? "New project"
              : modal === "task"
                ? "New task"
                : selected?.archived
                  ? "Restore project?"
                  : "Archive project?"
          }
          onClose={() => {
            if (!busy) setModal(null);
          }}
        >
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              const owner = currentRoute.current;
              const form = new FormData(event.currentTarget);
              setBusy(true);
              setError("");
              try {
                if (modal === "project") {
                  const created = await api<Project>("/api/projects", {
                    method: "POST",
                    json: {
                      name: form.get("name"),
                      description: form.get("description"),
                    },
                  });
                  if (owner === currentRoute.current) {
                    setModal(null);
                    projects.refresh();
                    navigate(created.id, null, false, false);
                  }
                } else if (modal === "task") {
                  const created = await api<Task>(
                    `/api/projects/${projectId}/tasks`,
                    { method: "POST", json: { title: form.get("title") } },
                  );
                  if (owner === currentRoute.current) {
                    setModal(null);
                    tasks.refresh();
                    navigate(projectId, created.id);
                  }
                } else if (selected) {
                  const updated = await api<Project>(
                    `/api/projects/${selected.id}`,
                    { method: "PATCH", json: { archived: !selected.archived } },
                  );
                  if (owner === currentRoute.current) {
                    setModal(null);
                    projectDetail.confirm(updated);
                    projects.refresh();
                    navigate(updated.id, null, updated.archived, false);
                  }
                }
              } catch (cause) {
                if (owner === currentRoute.current) setError(errorText(cause));
              } finally {
                if (owner === currentRoute.current) setBusy(false);
              }
            }}
          >
            <div className="modal-body">
              <ErrorBanner error={error} />
              {modal === "project" ? (
                <>
                  <Field label="Project name">
                    <input
                      name="name"
                      autoFocus
                      required
                      maxLength={100}
                      placeholder="e.g. Website launch"
                      disabled={busy}
                    />
                  </Field>
                  <Field
                    label="Description"
                    hint="Optional. A sentence to give your team direction."
                  >
                    <textarea
                      name="description"
                      maxLength={10000}
                      rows={3}
                      disabled={busy}
                      placeholder="What are we working toward?"
                    />
                  </Field>
                </>
              ) : modal === "task" ? (
                <Field label="Task title">
                  <input
                    name="title"
                    autoFocus
                    required
                    maxLength={200}
                    placeholder="What needs to get done?"
                    disabled={busy}
                  />
                </Field>
              ) : (
                <p>
                  {selected?.archived
                    ? "Bring this project back to your active workspace?"
                    : "Archive this project and pause new task changes? Your tasks and comments will be kept."}
                </p>
              )}
            </div>
            <footer className="modal-footer">
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() => setModal(null)}
              >
                Cancel
              </button>
              <button className="button primary" disabled={busy}>
                {busy ? (
                  <Spinner />
                ) : modal === "project" ? (
                  "Create project"
                ) : modal === "task" ? (
                  "Create task"
                ) : selected?.archived ? (
                  "Restore project"
                ) : (
                  "Archive project"
                )}
              </button>
            </footer>
          </form>
        </Modal>
      )}
    </div>
  );
}

function TaskDetail({
  id,
  projectId,
  users,
  readOnly,
  onClose,
  onSaved,
}: {
  id: number;
  projectId: number;
  users: User[];
  readOnly: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { api } = useAuth();
  const { setDirty, guard } = useDraftGuard();
  const [task, setTask] = useState<Task | null>(null);
  const [draft, setDraft] = useState<Task | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const [conflict, setConflict] = useState(false);
  const [comment, setComment] = useState("");
  const [commentBusy, setCommentBusy] = useState(false);
  const current = useRef(true);
  const epoch = useRef(0);
  const matching =
    task?.id === id &&
    draft?.id === id &&
    task.project_id === projectId &&
    draft.project_id === projectId;
  const comments = usePaged<Comment>(matching ? `/api/tasks/${id}/comments` : null);
  const taskFieldDirty = Boolean(
    task &&
    draft &&
    (task.title !== draft.title ||
      task.description !== draft.description ||
      task.status !== draft.status ||
      task.priority !== draft.priority ||
      task.assignee_id !== draft.assignee_id ||
      task.due_date !== draft.due_date),
  );
  const commentDraftDirty = comment.length > 0;
  const navigationDirty = taskFieldDirty || commentDraftDirty;
  useEffect(() => {
    setDirty(navigationDirty);
  }, [navigationDirty, setDirty]);
  useEffect(() => {
    current.current = true;
    return () => {
      current.current = false;
      setDirty(false);
    };
  }, [setDirty]);
  useEffect(() => {
    const request = ++epoch.current;
    const controller = new AbortController();
    setLoading(true);
    setTask(null);
    setDraft(null);
    setError("");
    setConflict(false);
    api<Task>(`/api/tasks/${id}`, { signal: controller.signal })
      .then((value) => {
        if (value.id !== id || value.project_id !== projectId) {
          throw new ApiError(
            "This task does not belong to the selected project. Close it and open the task from its project.",
          );
        }
        if (current.current && request === epoch.current) {
          setTask(value);
          setDraft(value);
          setLoading(false);
        }
      })
      .catch((cause) => {
        if (
          current.current &&
          request === epoch.current &&
          !(cause instanceof DOMException && cause.name === "AbortError")
        ) {
          setError(errorText(cause));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [api, id, projectId, version]);
  const edit = <K extends keyof Task>(key: K, value: Task[K]) =>
    setDraft((previous) =>
      previous ? { ...previous, [key]: value } : previous,
    );
  const save = async (archive = false) => {
    if (!matching || !task || !draft || saving) return;
    if ((archive && navigationDirty) || (!archive && !taskFieldDirty)) return;
    setSaving(true);
    setError("");
    const request = epoch.current;
    try {
      const saved = await api<Task>(`/api/tasks/${task.id}`, {
        method: "PATCH",
        json: {
          title: draft.title,
          description: draft.description,
          status: draft.status,
          priority: draft.priority,
          assignee_id: draft.assignee_id,
          due_date: draft.due_date,
          archived: archive ? !task.archived : task.archived,
          expected_revision: task.revision,
        },
      });
      if (current.current && request === epoch.current) {
        if (saved.id !== id || saved.project_id !== projectId) {
          throw new ApiError("The saved task does not match this project. Close it and reopen the task before continuing.");
        }
        setTask(saved);
        setDraft(saved);
        setConflict(false);
        onSaved();
        if (archive) onClose();
      }
    } catch (cause) {
      if (current.current && request === epoch.current) {
        setError(errorText(cause));
        setConflict(
          typeof cause === "object" &&
            cause !== null &&
            "status" in cause &&
            cause.status === 409,
        );
      }
    } finally {
      if (current.current && request === epoch.current) setSaving(false);
    }
  };
  return (
    <Modal title="Task details" onClose={onClose} wide>
      <div className="modal-body task-detail-body">
        {loading ? (
          <div className="loading-state">
            <Spinner label="Opening task" />
          </div>
        ) : matching && task && draft ? (
          <>
            <div className="task-detail-meta">
              <span className="task-id">TASK-{task.id}</span>
              <span className="muted">
                {taskFieldDirty
                  ? "Unsaved changes"
                  : commentDraftDirty
                    ? "Unsent comment"
                    : `Updated ${formatDate(task.updated_at)}`}
              </span>
            </div>
            <ErrorBanner error={error} />
            {conflict && (
              <button
                className="button small"
                onClick={() =>
                  guard(() => {
                    setComment("");
                    setDraft(null);
                    setLoading(true);
                    setVersion((value) => value + 1);
                  })
                }
              >
                Discard draft and load latest task
              </button>
            )}
            {readOnly && (
              <p className="notice">
                This project is archived. Restore the project to make changes.
              </p>
            )}
            <Field label="Task title">
              <input
                value={draft.title}
                onChange={(event) => edit("title", event.target.value)}
                maxLength={200}
                disabled={saving || readOnly || task.archived}
              />
            </Field>
            <Field label="Description">
              <textarea
                value={draft.description}
                onChange={(event) => edit("description", event.target.value)}
                maxLength={200000}
                rows={5}
                placeholder="Add some context for your team…"
                disabled={saving || readOnly || task.archived}
              />
            </Field>
            <div className="form-grid">
              <Field label="Status">
                <select
                  value={draft.status}
                  onChange={(event) => edit("status", event.target.value)}
                  disabled={saving || readOnly || task.archived}
                >
                  {catalog.task_statuses.map((status) => (
                    <option key={status.id} value={status.id}>
                      {status.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Assignee">
                <select
                  value={draft.assignee_id ?? ""}
                  onChange={(event) =>
                    edit(
                      "assignee_id",
                      event.target.value ? Number(event.target.value) : null,
                    )
                  }
                  disabled={saving || readOnly || task.archived}
                >
                  <option value="">Unassigned</option>
                  {users
                    .filter(
                      (user) => user.active || user.id === draft.assignee_id,
                    )
                    .map((user) => (
                      <option key={user.id} value={user.id}>
                        {user.display_name}
                        {!user.active ? " (inactive)" : ""}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label="Due date">
                <input
                  type="date"
                  value={draft.due_date ?? ""}
                  onChange={(event) =>
                    edit("due_date", event.target.value || null)
                  }
                  disabled={saving || readOnly || task.archived}
                />
              </Field>
              <Field label="Priority">
                <select
                  value={draft.priority}
                  onChange={(event) => edit("priority", event.target.value)}
                  disabled={saving || readOnly || task.archived}
                >
                  {catalog.task_priorities.map((priority) => (
                    <option key={priority.id} value={priority.id}>
                      {priority.label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <section className="comments">
              <h3>
                <MessageSquare size={17} />
                Comments
              </h3>
              <ErrorBanner error={comments.error} />
              {comments.items.map((item) => (
                <article className="comment" key={item.id}>
                  <Avatar
                    user={users.find((user) => user.id === item.author_id)}
                    small
                  />
                  <div>
                    <div className="comment-author">
                      <strong>
                        {users.find((user) => user.id === item.author_id)
                          ?.display_name ?? `Teammate ${item.author_id}`}
                      </strong>
                      <small>{formatDate(item.created_at)}</small>
                    </div>
                    <p>{item.body}</p>
                  </div>
                </article>
              ))}
              <LoadMore
                next={comments.next}
                loading={comments.loading}
                onClick={() => void comments.loadMore()}
              />
              {!readOnly && !task.archived && (
                <form
                  className="comment-form"
                  onSubmit={async (event) => {
                    event.preventDefault();
                    if (!comment.trim() || commentBusy) return;
                    setCommentBusy(true);
                    setError("");
                    try {
                      await api(`/api/tasks/${task.id}/comments`, {
                        method: "POST",
                        json: { body: comment },
                      });
                      if (current.current) {
                        setComment("");
                        comments.refresh();
                      }
                    } catch (cause) {
                      if (current.current) setError(errorText(cause));
                    } finally {
                      if (current.current) setCommentBusy(false);
                    }
                  }}
                >
                  <label className="visually-hidden" htmlFor="task-comment">
                    Write a comment
                  </label>
                  <textarea
                    id="task-comment"
                    value={comment}
                    onChange={(event) => setComment(event.target.value)}
                    maxLength={10000}
                    rows={2}
                    placeholder="Add to the conversation…"
                    disabled={commentBusy}
                  />
                  <button
                    className="button small"
                    disabled={commentBusy || !comment.trim()}
                  >
                    {commentBusy ? <Spinner /> : "Add comment"}
                  </button>
                </form>
              )}
            </section>
          </>
        ) : (
          <ErrorBanner error={error || "This task is unavailable."} />
        )}
      </div>
      <footer className="modal-footer">
        {matching && task && !readOnly && (
          <button
            className="button subtle"
            disabled={saving || navigationDirty}
            title={
              navigationDirty
                ? "Save task changes and post or clear your comment before archiving."
                : undefined
            }
            onClick={() => void save(true)}
          >
            {task.archived ? <Undo2 size={15} /> : <Archive size={15} />}
            {task.archived ? "Restore task" : "Archive task"}
          </button>
        )}
        <span className="spacer" />
        <button className="button" onClick={onClose}>
          Close
        </button>
        {matching && task && !task.archived && !readOnly && (
          <button
            className="button primary"
            disabled={saving || !taskFieldDirty || !draft?.title.trim()}
            onClick={() => void save()}
          >
            {saving ? <Spinner /> : "Save changes"}
          </button>
        )}
      </footer>
    </Modal>
  );
}
