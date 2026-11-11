import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  ChevronRight,
  File,
  Folder,
  FolderPlus,
  HardDrive,
  Link,
  Pencil,
  Search,
  Trash2,
  Undo2,
} from "lucide-react";
import {
  bytes,
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
  usePaged,
} from "./core";
import type { FileItem } from "./types";

export function Files() {
  const { api } = useAuth();
  const [parameters, setParameters] = useSearchParams();
  const routeKey = parameters.toString();
  const currentRoute = useRef(routeKey);
  currentRoute.current = routeKey;
  const modalRoute = useRef(routeKey);
  const parent = parameters.get("folder")
    ? Number(parameters.get("folder"))
    : null;
  const trash = parameters.get("view") === "trash";
  const [search, setSearch] = useState("");
  const list = usePaged<FileItem>(
    query("/api/files", { parent_id: parent, trash, q: search }),
  );
  const [modal, setModalState] = useState<{
    mode: "folder" | "rename" | "trash";
    item?: FileItem;
  } | null>(null);
  const setModal = (value: typeof modal) => {
    modalRoute.current = currentRoute.current;
    setModalState(value);
  };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uploading, setUploading] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const [linkedFile, setLinkedFile] = useState<FileItem | null>(null);
  const linkedId = parameters.get("file");
  useEffect(() => {
    setModalState(null);
    setBusy(false);
    setError("");
    setNotice("");
  }, [routeKey]);
  useEffect(() => {
    let current = true;
    setLinkedFile(null);
    if (linkedId)
      api<FileItem>(`/api/files/${linkedId}`)
        .then((item) => {
          if (current) setLinkedFile(item);
        })
        .catch((cause) => {
          if (current) setError(errorText(cause));
        });
    return () => {
      current = false;
    };
  }, [api, linkedId]);
  const breadcrumbs = list.extra.breadcrumbs as
    { id: number | null; name: string }[] | undefined;
  const goFolder = (id: number | null) => {
    if (uploading) return;
    setSearch("");
    setError("");
    setNotice("");
    setParameters(id ? { folder: String(id) } : {});
  };
  const download = async (item: FileItem) => {
    setError("");
    try {
      const response = await fetch(`/api/files/${item.id}/download`, {
        credentials: "same-origin",
      });
      if (!response.ok) {
        if (response.status === 401) await api("/api/me");
        let detail = "Could not download this file.";
        try {
          const body = (await response.json()) as { detail?: string };
          detail = body.detail ?? detail;
        } catch {
          /* Keep actionable text. */
        }
        throw new Error(detail);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = item.name;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(errorText(cause));
    }
  };
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setError("");
    setNotice("");
    let complete = 0;
    const failures: string[] = [];
    for (const file of Array.from(files)) {
      const form = new FormData();
      form.append("file", file);
      if (parent !== null) form.append("parent_id", String(parent));
      try {
        await api("/api/files/upload", { method: "POST", body: form });
        complete++;
      } catch (cause) {
        failures.push(`${file.name}: ${errorText(cause)}`);
      }
    }
    list.refresh();
    setUploading(false);
    setNotice(
      complete
        ? `${complete} ${complete === 1 ? "file" : "files"} uploaded.`
        : "",
    );
    setError(failures.join(" "));
    if (uploadInput.current) uploadInput.current.value = "";
  };
  const copyLink = async (item: FileItem) => {
    try {
      const url = new URL(location.href);
      url.search = `?file=${item.id}`;
      await navigator.clipboard.writeText(url.href);
      setNotice("Member-only link copied. Teammates must sign in to open it.");
    } catch {
      setError(
        "Your browser could not copy the link. Select and copy the address from your browser.",
      );
    }
  };
  return (
    <div className="app-layout">
      <aside className="context-sidebar">
        <p className="nav-caption">WORKSPACE</p>
        <button
          className={`nav-item ${!trash ? "active" : ""}`}
          disabled={uploading}
          onClick={() => goFolder(null)}
        >
          <HardDrive size={18} />
          All files
        </button>
        <button
          className={`nav-item ${trash ? "active" : ""}`}
          disabled={uploading}
          onClick={() => {
            setSearch("");
            setParameters({ view: "trash" });
          }}
        >
          <Trash2 size={18} />
          Trash
        </button>
        <div className="sidebar-bottom">
          <HardDrive size={17} />
          <div>
            <strong>{bytes(Number(list.extra.usage_bytes ?? 0))} stored</strong>
            <small>Including items in Trash</small>
          </div>
        </div>
      </aside>
      <main className="main-panel">
        <header className="page-header">
          <div>
            <span className="eyebrow">A PLACE FOR EVERYTHING</span>
            <h1>
              {trash
                ? "Trash"
                : parent
                  ? (breadcrumbs?.at(-1)?.name ?? "Folder")
                  : "Files"}
            </h1>
            <p className="muted">
              {trash
                ? "A second chance for things you still need."
                : "Keep your team’s files together. Find what matters."}
            </p>
          </div>
          {!trash && (
            <div className="header-actions">
              <button
                className="button"
                disabled={uploading}
                onClick={() => {
                  setError("");
                  setModal({ mode: "folder" });
                }}
              >
                <FolderPlus size={17} />
                New folder
              </button>
              <button
                className="button primary"
                disabled={uploading}
                onClick={() => uploadInput.current?.click()}
              >
                {uploading ? <Spinner /> : <ArrowUpFromLine size={17} />}
                {uploading ? "Uploading…" : "Upload files"}
              </button>
              <input
                ref={uploadInput}
                className="visually-hidden"
                type="file"
                multiple
                aria-label="Choose files to upload"
                onChange={(event) => void upload(event.target.files)}
              />
            </div>
          )}
        </header>
        <div className="toolbar">
          <div className="breadcrumbs">
            <button disabled={uploading} onClick={() => goFolder(null)}>
              {trash ? "Trash" : "All files"}
            </button>
            {!trash &&
              breadcrumbs
                ?.filter((crumb) => crumb.id !== null)
                .map((crumb) => (
                  <span key={crumb.id}>
                    <ChevronRight size={14} />
                    <button
                      disabled={uploading}
                      onClick={() => goFolder(crumb.id)}
                    >
                      {crumb.name}
                    </button>
                  </span>
                ))}
          </div>
          <label className="search-box">
            <Search size={17} />
            <input
              type="search"
              placeholder="Search files"
              aria-label="Search files"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
        </div>
        <ErrorBanner error={error || list.error} />
        {notice && (
          <div className="notice" role="status">
            {notice}
          </div>
        )}
        {linkedFile && (
          <div className="linked-file">
            <File size={19} />
            <span>{linkedFile.name}</span>
            <button
              className="button small"
              onClick={() =>
                linkedFile.kind === "folder"
                  ? goFolder(linkedFile.id)
                  : void download(linkedFile)
              }
            >
              {linkedFile.kind === "folder" ? "Open folder" : "Download file"}
            </button>
            <button
              className="text-button"
              onClick={() => {
                const next = new URLSearchParams(parameters);
                next.delete("file");
                setParameters(next);
              }}
            >
              Dismiss
            </button>
          </div>
        )}
        <section className="content-card file-card">
          {list.loading && !list.items.length ? (
            <div className="loading-state">
              <Spinner label="Loading files" />
            </div>
          ) : list.items.length ? (
            <div className="table-scroll">
              <table className="file-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th className="hide-mobile">Size</th>
                    <th className="hide-mobile">Modified</th>
                    <th>
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <button
                          className="file-name"
                          disabled={uploading}
                          onClick={() =>
                            !trash &&
                            (item.kind === "folder"
                              ? goFolder(item.id)
                              : void download(item))
                          }
                        >
                          <span className={`file-type-icon ${item.kind}`}>
                            {item.kind === "folder" ? (
                              <Folder size={22} />
                            ) : (
                              <File size={22} />
                            )}
                          </span>
                          <span>
                            {item.name}
                            <small className="mobile-only">
                              {item.kind === "folder"
                                ? "Folder"
                                : bytes(item.size)}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td className="hide-mobile muted">
                        {item.kind === "folder" ? "—" : bytes(item.size)}
                      </td>
                      <td className="hide-mobile muted">
                        {formatDate(item.updated_at)}
                      </td>
                      <td>
                        <div className="row-actions">
                          {trash ? (
                            <button
                              className="button small"
                              disabled={busy}
                              onClick={async () => {
                                setBusy(true);
                                setError("");
                                try {
                                  await api(`/api/files/${item.id}/restore`, {
                                    method: "POST",
                                  });
                                  list.refresh();
                                  setNotice(`${item.name} restored.`);
                                } catch (cause) {
                                  setError(errorText(cause));
                                } finally {
                                  setBusy(false);
                                }
                              }}
                            >
                              <Undo2 size={15} />
                              Restore
                            </button>
                          ) : (
                            <>
                              <button
                                className="icon-button"
                                aria-label={`Copy link to ${item.name}`}
                                title="Copy member-only link"
                                onClick={() => void copyLink(item)}
                              >
                                <Link size={16} />
                              </button>
                              {item.kind === "file" && (
                                <button
                                  className="icon-button"
                                  aria-label={`Download ${item.name}`}
                                  title="Download"
                                  onClick={() => void download(item)}
                                >
                                  <ArrowDownToLine size={16} />
                                </button>
                              )}
                              <button
                                className="icon-button"
                                aria-label={`Rename ${item.name}`}
                                title="Rename"
                                onClick={() => {
                                  setError("");
                                  setModal({ mode: "rename", item });
                                }}
                              >
                                <Pencil size={16} />
                              </button>
                              <button
                                className="icon-button danger-text"
                                aria-label={`Move ${item.name} to Trash`}
                                title="Move to Trash"
                                onClick={() => {
                                  setError("");
                                  setModal({ mode: "trash", item });
                                }}
                              >
                                <Trash2 size={16} />
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              title={
                search
                  ? "No matching files"
                  : trash
                    ? "Trash is empty"
                    : "Room for something useful"
              }
              description={
                search
                  ? "Try another filename."
                  : trash
                    ? "Items you move to Trash will stay here until you restore them."
                    : "Upload your first file or create a folder to get organized."
              }
              action={
                !trash && !search ? (
                  <button
                    className="button primary"
                    onClick={() => uploadInput.current?.click()}
                  >
                    <ArrowUpFromLine size={17} />
                    Upload files
                  </button>
                ) : undefined
              }
            />
          )}
          <LoadMore
            next={list.next}
            loading={list.loading}
            onClick={() => void list.loadMore()}
          />
        </section>
        <p className="content-footnote">
          Private to your workspace. Files stay on your server.
        </p>
      </main>
      {modal && (
        <Modal
          title={
            modal.mode === "folder"
              ? "New folder"
              : modal.mode === "rename"
                ? "Rename item"
                : "Move to Trash?"
          }
          onClose={() => {
            if (!busy) setModal(null);
          }}
        >
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              const owner = modalRoute.current;
              if (owner !== currentRoute.current) return;
              const form = new FormData(event.currentTarget);
              setBusy(true);
              setError("");
              try {
                if (modal.mode === "folder")
                  await api("/api/files/folders", {
                    method: "POST",
                    json: { name: form.get("name"), parent_id: parent },
                  });
                else if (modal.mode === "rename")
                  await api(`/api/files/${modal.item!.id}`, {
                    method: "PATCH",
                    json: { name: form.get("name") },
                  });
                else
                  await api(`/api/files/${modal.item!.id}`, {
                    method: "DELETE",
                  });
                if (owner === currentRoute.current) {
                  setModal(null);
                  list.refresh();
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
              {modal.mode === "trash" ? (
                <p>
                  Move <strong>{modal.item?.name}</strong> to Trash? You can
                  restore it later. Folders must have no active items.
                </p>
              ) : (
                <Field label={modal.mode === "folder" ? "Folder name" : "Name"}>
                  <input
                    name="name"
                    autoFocus
                    required
                    maxLength={180}
                    defaultValue={modal.item?.name ?? ""}
                    disabled={busy}
                    placeholder="e.g. Team resources"
                  />
                </Field>
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
              <button
                className={`button ${modal.mode === "trash" ? "danger" : "primary"}`}
                disabled={busy}
              >
                {busy ? (
                  <Spinner />
                ) : modal.mode === "folder" ? (
                  "Create folder"
                ) : modal.mode === "rename" ? (
                  "Save name"
                ) : (
                  "Move to Trash"
                )}
              </button>
            </footer>
          </form>
        </Modal>
      )}
    </div>
  );
}
