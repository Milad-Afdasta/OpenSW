import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ArrowDown,
  ChevronUp,
  Hash,
  MessageCircle,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Send,
  Trash2,
  Users,
} from "lucide-react";
import {
  Avatar,
  EmptyState,
  ErrorBanner,
  errorText,
  Field,
  formatDate,
  formatTime,
  LoadMore,
  Modal,
  Spinner,
  useAuth,
  usePaged,
  useResource,
} from "./core";
import type { Conversation, Message, User } from "./types";

export function Chat({ users }: { users: User[] }) {
  const { api, session, suspended } = useAuth();
  const [parameters, setParameters] = useSearchParams();
  const selectedId = parameters.get("conversation")
    ? Number(parameters.get("conversation"))
    : null;
  const channels = usePaged<Conversation>("/api/chat/channels");
  const dms = usePaged<Conversation>("/api/chat/dms");
  const conversationDetail = useResource<Conversation>(
    "/api/chat/conversations",
    selectedId,
  );
  const selected = conversationDetail.item;
  const matching = selected !== null && selected.id === selectedId;
  const [search, setSearch] = useState("");
  const [before, setBefore] = useState<number | null>(null);
  const [page, setPage] = useState<{
    key: string;
    items: Message[];
    has_more: boolean;
    loading: boolean;
  }>({ key: "", items: [], has_more: false, loading: false });
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [sendErrors, setSendErrors] = useState<Record<number, string>>({});
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [pendingSends, setPendingSends] = useState<Record<number, boolean>>({});
  const pendingSendIds = useRef(new Set<number>());
  const sending = selectedId !== null && Boolean(pendingSends[selectedId]);
  const [modal, setModal] = useState<"channel" | "dm" | null>(null);
  const [editMessage, setEditMessage] = useState<Message | null>(null);
  const [deleteMessage, setDeleteMessage] = useState<Message | null>(null);
  const [busy, setBusy] = useState(false);
  const messageList = useRef<HTMLDivElement>(null);
  const epoch = useRef(0);
  const atBottom = useRef(true);
  const readAttempt = useRef(0);
  const key = `${session.user.id}:${selectedId}:${search}:${before}`;
  const currentKey = useRef(key);
  currentKey.current = key;
  const currentSelected = useRef(selectedId);
  currentSelected.current = selectedId;
  const messages = page.key === key ? page.items : [];
  const draft = selectedId === null ? "" : (drafts[selectedId] ?? "");
  const sendError = selectedId === null ? "" : (sendErrors[selectedId] ?? "");
  useEffect(() => {
    setBefore(null);
    setSearch("");
    setModal(null);
    setEditMessage(null);
    setDeleteMessage(null);
    setBusy(false);
    atBottom.current = true;
    readAttempt.current = 0;
  }, [selectedId]);
  useEffect(() => {
    const requestEpoch = ++epoch.current;
    const controller = new AbortController();
    let inFlight = false;
    setError("");
    setPage({ key, items: [], has_more: false, loading: selectedId !== null });
    const load = async (initial = false) => {
      if (
        selectedId === null ||
        !matching ||
        suspended ||
        inFlight ||
        (!initial &&
          (document.visibilityState !== "visible" || !document.hasFocus()))
      )
        return;
      inFlight = true;
      const query = new URLSearchParams();
      if (before !== null) query.set("before_id", String(before));
      if (search) query.set("q", search);
      try {
        const result = await api<{ items: Message[]; has_more: boolean }>(
          `/api/chat/conversations/${selectedId}/messages${query.size ? `?${query}` : ""}`,
          { signal: controller.signal },
        );
        if (requestEpoch === epoch.current && currentKey.current === key) {
          setPage({ key, ...result, loading: false });
          setError("");
        }
      } catch (cause) {
        if (
          requestEpoch === epoch.current &&
          !(cause instanceof DOMException && cause.name === "AbortError")
        ) {
          setError(errorText(cause));
          setPage((previous) => ({ ...previous, loading: false }));
        }
      } finally {
        inFlight = false;
      }
    };
    void load(true);
    const timer = window.setInterval(() => void load(), 2500);
    const visible = () => {
      if (document.visibilityState === "visible" && document.hasFocus())
        void load();
    };
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("focus", visible);
    return () => {
      controller.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("focus", visible);
    };
  }, [api, key, selectedId, before, search, refresh, suspended, matching]);
  useLayoutEffect(() => {
    if (atBottom.current && before === null && !search && messageList.current)
      messageList.current.scrollTop = messageList.current.scrollHeight;
  }, [messages, before, search]);
  const markRead = () => {
    if (
      suspended ||
      selectedId === null ||
      !matching ||
      before !== null ||
      search ||
      document.visibilityState !== "visible" ||
      !document.hasFocus() ||
      !atBottom.current ||
      !messages.length
    )
      return;
    const last = messages.at(-1)!.id;
    if (last <= readAttempt.current) return;
    readAttempt.current = last;
    void api(`/api/chat/conversations/${selectedId}/read`, {
      method: "POST",
      json: { message_id: last },
    })
      .then(() => {
        channels.refresh();
        dms.refresh();
      })
      .catch(() => {
        /* Read receipt is best effort; do not automatically replay an unknown write. */
      });
  };
  useEffect(() => {
    markRead();
  }, [page]);
  const select = (id: number) => {
    setParameters({ conversation: String(id) });
  };
  const send = async () => {
    if (
      selectedId === null ||
      !matching ||
      (selected.peer !== null && !selected.peer.active) ||
      !draft.trim() ||
      pendingSendIds.current.has(selectedId)
    )
      return;
    const target = selectedId;
    const text = draft;
    pendingSendIds.current.add(target);
    setPendingSends((previous) => ({ ...previous, [target]: true }));
    setSendErrors((previous) => ({ ...previous, [target]: "" }));
    try {
      await api<Message>(`/api/chat/conversations/${target}/messages`, {
        method: "POST",
        json: { body: text },
      });
      setDrafts((previous) => ({
        ...previous,
        [target]: "",
      }));
      setSendErrors((previous) => ({ ...previous, [target]: "" }));
      if (currentSelected.current === target) {
        setBefore(null);
        setSearch("");
        atBottom.current = true;
        setRefresh((value) => value + 1);
        channels.refresh();
        dms.refresh();
      }
    } catch (cause) {
      setSendErrors((previous) => ({
        ...previous,
        [target]: errorText(cause),
      }));
    } finally {
      pendingSendIds.current.delete(target);
      setPendingSends((previous) => ({ ...previous, [target]: false }));
    }
  };
  return (
    <div className="app-layout chat-layout">
      <aside className="context-sidebar">
        <div className="sidebar-heading">
          <span className="nav-caption">CHANNELS</span>
          <div className="row-actions">
            <button
              className="icon-button"
              aria-label="Refresh conversations"
              title="Refresh conversations"
              onClick={() => {
                channels.refresh();
                dms.refresh();
              }}
            >
              <RefreshCw size={15} />
            </button>
            <button
              className="icon-button"
              aria-label="New channel"
              onClick={() => {
                setError("");
                setModal("channel");
              }}
            >
              <Plus size={18} />
            </button>
          </div>
        </div>
        <nav className="page-list" aria-label="Channels">
          {channels.items.map((channel) => (
            <button
              className={`conversation-item ${selectedId === channel.id ? "active" : ""}`}
              key={channel.id}
              onClick={() => select(channel.id)}
            >
              <Hash size={17} />
              <span>{channel.name}</span>
              {channel.unread_count > 0 && (
                <span className="unread-count">{channel.unread_count}</span>
              )}
            </button>
          ))}
          <LoadMore
            next={channels.next}
            loading={channels.loading}
            onClick={() => void channels.loadMore()}
          />
        </nav>
        <div className="sidebar-heading dm-heading">
          <span className="nav-caption">DIRECT MESSAGES</span>
          <button
            className="icon-button"
            aria-label="New direct message"
            onClick={() => {
              setError("");
              setModal("dm");
            }}
          >
            <Plus size={18} />
          </button>
        </div>
        <nav className="page-list" aria-label="Direct messages">
          {dms.items.map((dm) => (
            <button
              className={`conversation-item ${selectedId === dm.id ? "active" : ""}`}
              key={dm.id}
              onClick={() => select(dm.id)}
            >
              <Avatar user={dm.peer ?? undefined} small />
              <span>{dm.name}</span>
              {dm.unread_count > 0 && (
                <span className="unread-count">{dm.unread_count}</span>
              )}
            </button>
          ))}
          <LoadMore
            next={dms.next}
            loading={dms.loading}
            onClick={() => void dms.loadMore()}
          />
          {!dms.items.length && (
            <p className="sidebar-empty">
              Good conversations start with hello.
            </p>
          )}
        </nav>
        <ErrorBanner error={channels.error || dms.error} />
        <div className="sidebar-bottom">
          <MessageCircle size={19} />
          <div>
            <strong>Conversations that stay yours</strong>
            <small>No distractions. Just your team.</small>
          </div>
        </div>
      </aside>
      <main className="main-panel chat-main">
        {selectedId === null ? (
          <>
            <header className="page-header">
              <div>
                <span className="eyebrow">
                  A LITTLE CLOSER, EVEN FROM AFAR.
                </span>
                <h1>
                  Your team’s place
                  <br className="desktop-only" /> to talk things through.
                </h1>
                <p className="muted">
                  Keep conversations together and make space for good work.
                </p>
              </div>
            </header>
            <EmptyState
              title="Say something useful"
              description="Choose a channel or start a private conversation with a teammate."
              action={
                <button
                  className="button primary"
                  onClick={() => setModal("dm")}
                >
                  <MessageCircle size={17} />
                  New message
                </button>
              }
            />
          </>
        ) : !matching ? (
          <>
            <ErrorBanner error={conversationDetail.error}>
              <button
                className="text-button"
                onClick={conversationDetail.refresh}
              >
                Reload conversation
              </button>
            </ErrorBanner>
            {conversationDetail.loading && (
              <div className="loading-state">
                <Spinner label="Loading conversation" />
              </div>
            )}
          </>
        ) : (
          <>
            <header className="conversation-header">
              <div className="conversation-title">
                {selected?.kind === "dm" ? (
                  <Avatar user={selected.peer ?? undefined} />
                ) : (
                  <span className="channel-symbol">
                    <Hash size={22} />
                  </span>
                )}
                <div>
                  <h1>{selected.name}</h1>
                  <p>
                    {selected?.kind === "dm"
                      ? "Only the two of you can see this conversation."
                      : "A shared space for your team."}
                  </p>
                </div>
              </div>
              <label className="search-box">
                <Search size={17} />
                <input
                  type="search"
                  placeholder="Search conversation"
                  aria-label="Search conversation"
                  value={search}
                  onChange={(event) => {
                    setBefore(null);
                    setSearch(event.target.value);
                  }}
                />
              </label>
            </header>
            <ErrorBanner error={error} />
            <div
              className="messages"
              ref={messageList}
              onScroll={() => {
                const element = messageList.current;
                if (element)
                  atBottom.current =
                    element.scrollHeight -
                      element.scrollTop -
                      element.clientHeight <
                    48;
                if (atBottom.current) markRead();
              }}
            >
              <div className="history-actions">
                {before !== null && (
                  <button
                    className="button small"
                    onClick={() => {
                      atBottom.current = true;
                      setBefore(null);
                    }}
                  >
                    <ArrowDown size={14} />
                    Latest messages
                  </button>
                )}
                {page.key === key && page.has_more && (
                  <button
                    className="button subtle small"
                    onClick={() => {
                      atBottom.current = false;
                      setBefore(messages[0]?.id ?? null);
                    }}
                  >
                    <ChevronUp size={15} />
                    Older messages
                  </button>
                )}
              </div>
              {page.loading || page.key !== key ? (
                <div className="loading-state">
                  <Spinner label="Loading conversation" />
                </div>
              ) : messages.length ? (
                messages.map((message, index) => {
                  const author = users.find(
                    (user) => user.id === message.author_id,
                  );
                  const showDay =
                    index === 0 ||
                    formatDate(messages[index - 1].created_at) !==
                      formatDate(message.created_at);
                  return (
                    <div key={message.id}>
                      {showDay && (
                        <div className="message-day">
                          <span>{formatDate(message.created_at)}</span>
                        </div>
                      )}
                      <article
                        className={`message ${message.author_id === session.user.id ? "own" : ""}`}
                      >
                        <Avatar user={author} />
                        <div className="message-content">
                          <header>
                            <strong>
                              {author?.display_name ??
                                `Teammate ${message.author_id}`}
                            </strong>
                            <time title={message.created_at}>
                              {formatTime(message.created_at)}
                            </time>
                            {message.edited_at && !message.deleted && (
                              <span className="edited-label">edited</span>
                            )}
                          </header>
                          <p
                            className={message.deleted ? "deleted-message" : ""}
                          >
                            {message.deleted
                              ? "This message was deleted."
                              : message.body}
                          </p>
                        </div>
                        {message.author_id === session.user.id &&
                          !message.deleted && (
                            <div className="message-actions">
                              <button
                                className="icon-button"
                                aria-label={`Edit message ${message.id}`}
                                title="Edit message"
                                onClick={() => {
                                  setError("");
                                  setEditMessage(message);
                                }}
                              >
                                <Pencil size={14} />
                              </button>
                              <button
                                className="icon-button danger-text"
                                aria-label={`Delete message ${message.id}`}
                                title="Delete message"
                                onClick={() => {
                                  setError("");
                                  setDeleteMessage(message);
                                }}
                              >
                                <Trash2 size={14} />
                              </button>
                            </div>
                          )}
                      </article>
                    </div>
                  );
                })
              ) : (
                <EmptyState
                  title={
                    search
                      ? "No matching messages"
                      : "The start of a good conversation"
                  }
                  description={
                    search
                      ? "Try another word or phrase."
                      : "Say hello, ask a question, or share what’s on your mind."
                  }
                />
              )}
            </div>
            <div className="composer-wrap">
              <ErrorBanner error={sendError}>
                {sendError && (
                  <button
                    className="text-button"
                    onClick={() => setRefresh((value) => value + 1)}
                  >
                    Refresh conversation to check delivery
                  </button>
                )}
              </ErrorBanner>
              {selected?.peer && !selected.peer.active ? (
                <p className="notice">
                  This teammate is inactive. Your conversation is still here to
                  read.
                </p>
              ) : (
                <form
                  className="composer"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void send();
                  }}
                >
                  <label className="visually-hidden" htmlFor="message-body">
                    Message
                  </label>
                  <textarea
                    id="message-body"
                    aria-label="Message"
                    placeholder={
                      selected?.kind === "dm"
                        ? `Message ${selected.name}…`
                        : `Message ${selected?.name ?? "your team"}…`
                    }
                    value={draft}
                    maxLength={4000}
                    rows={2}
                    disabled={sending}
                    onChange={(event) =>
                      setDrafts((previous) => ({
                        ...previous,
                        [selectedId]: event.target.value,
                      }))
                    }
                    onKeyDown={(event) => {
                      if (
                        event.key === "Enter" &&
                        !event.shiftKey &&
                        !event.nativeEvent.isComposing
                      ) {
                        event.preventDefault();
                        void send();
                      }
                    }}
                  />
                  <footer>
                    <span>Enter to send · Shift + Enter for a new line</span>
                    <button
                      className="button primary small"
                      disabled={sending || !draft.trim()}
                    >
                      {sending ? <Spinner /> : <Send size={16} />}Send
                    </button>
                  </footer>
                </form>
              )}
            </div>
          </>
        )}
      </main>
      {modal && (
        <Modal
          title={modal === "channel" ? "New channel" : "New direct message"}
          onClose={() => {
            if (!busy) setModal(null);
          }}
        >
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              const owner = currentKey.current;
              const form = new FormData(event.currentTarget);
              setBusy(true);
              setError("");
              try {
                const conversation = await api<Conversation>(
                  modal === "channel" ? "/api/chat/channels" : "/api/chat/dms",
                  {
                    method: "POST",
                    json:
                      modal === "channel"
                        ? { name: form.get("name") }
                        : { user_id: Number(form.get("user_id")) },
                  },
                );
                if (owner === currentKey.current) {
                  setModal(null);
                  channels.refresh();
                  dms.refresh();
                  select(conversation.id);
                }
              } catch (cause) {
                if (owner === currentKey.current) setError(errorText(cause));
              } finally {
                if (owner === currentKey.current) setBusy(false);
              }
            }}
          >
            <div className="modal-body">
              <ErrorBanner error={error} />
              {modal === "channel" ? (
                <>
                  <Field label="Channel name">
                    <input
                      name="name"
                      autoFocus
                      required
                      maxLength={80}
                      placeholder="e.g. design-feedback"
                      disabled={busy}
                    />
                  </Field>
                  <p className="muted">
                    Everyone in your workspace can join the conversation.
                  </p>
                </>
              ) : (
                <>
                  <Field label="Teammate">
                    <select
                      name="user_id"
                      required
                      autoFocus
                      defaultValue=""
                      disabled={busy}
                    >
                      <option value="" disabled>
                        Choose a teammate
                      </option>
                      {users
                        .filter(
                          (user) => user.active && user.id !== session.user.id,
                        )
                        .map((user) => (
                          <option value={user.id} key={user.id}>
                            {user.display_name} · {user.username}
                          </option>
                        ))}
                    </select>
                  </Field>
                  <p className="muted">
                    <Users size={15} /> Only you and this teammate can read
                    these messages.
                  </p>
                </>
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
                ) : modal === "channel" ? (
                  "Create channel"
                ) : (
                  "Start conversation"
                )}
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {editMessage && (
        <Modal
          title="Edit message"
          onClose={() => {
            if (!busy) setEditMessage(null);
          }}
        >
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              const owner = currentKey.current;
              const form = new FormData(event.currentTarget);
              setBusy(true);
              setError("");
              try {
                await api(`/api/chat/messages/${editMessage.id}`, {
                  method: "PATCH",
                  json: { body: form.get("body") },
                });
                if (owner === currentKey.current) {
                  setEditMessage(null);
                  setRefresh((value) => value + 1);
                }
              } catch (cause) {
                if (owner === currentKey.current) setError(errorText(cause));
              } finally {
                if (owner === currentKey.current) setBusy(false);
              }
            }}
          >
            <div className="modal-body">
              <ErrorBanner error={error} />
              <Field label="Message text">
                <textarea
                  name="body"
                  autoFocus
                  required
                  maxLength={4000}
                  rows={4}
                  defaultValue={editMessage.body}
                  disabled={busy}
                />
              </Field>
            </div>
            <footer className="modal-footer">
              <button
                className="button"
                type="button"
                disabled={busy}
                onClick={() => setEditMessage(null)}
              >
                Cancel
              </button>
              <button className="button primary" disabled={busy}>
                {busy ? <Spinner /> : "Save message"}
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {deleteMessage && (
        <Modal
          title="Delete message?"
          onClose={() => {
            if (!busy) setDeleteMessage(null);
          }}
        >
          <div className="modal-body">
            <ErrorBanner error={error} />
            <p>
              This message will be replaced with a deleted-message placeholder.
            </p>
          </div>
          <footer className="modal-footer">
            <button
              className="button"
              disabled={busy}
              onClick={() => setDeleteMessage(null)}
            >
              Cancel
            </button>
            <button
              className="button danger"
              disabled={busy}
              onClick={async () => {
                const owner = currentKey.current;
                setBusy(true);
                setError("");
                try {
                  await api(`/api/chat/messages/${deleteMessage.id}`, {
                    method: "DELETE",
                  });
                  if (owner === currentKey.current) {
                    setDeleteMessage(null);
                    setRefresh((value) => value + 1);
                  }
                } catch (cause) {
                  if (owner === currentKey.current) setError(errorText(cause));
                } finally {
                  if (owner === currentKey.current) setBusy(false);
                }
              }}
            >
              {busy ? <Spinner /> : "Delete message"}
            </button>
          </footer>
        </Modal>
      )}
    </div>
  );
}
