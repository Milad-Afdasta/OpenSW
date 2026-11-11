import catalog from "../../shared/catalog.json";

export { catalog };
export type AppId = "files" | "projects" | "notes" | "chat";
export type User = {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "member";
  active: boolean;
};
export type Session = {
  user: User;
  csrf_token: string;
  app: AppId;
  team_name: string;
  catalog: typeof catalog;
};
export type Page<T> = { items: T[]; next_cursor: number | null };
export type FileItem = {
  id: number;
  name: string;
  kind: "file" | "folder";
  parent_id: number | null;
  size: number;
  created_by: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
};
export type FilePage = Page<FileItem> & {
  breadcrumbs: { id: number | null; name: string }[];
  usage_bytes: number;
};
export type Project = {
  id: number;
  name: string;
  description: string;
  archived: boolean;
  created_by: number;
  created_at: string;
  updated_at: string;
};
export type TaskSummary = {
  id: number;
  project_id: number;
  title: string;
  status: string;
  priority: string;
  assignee_id: number | null;
  due_date: string | null;
  archived: boolean;
  revision: number;
  created_by: number;
  created_at: string;
  updated_at: string;
};
export type Task = TaskSummary & { description: string };
export type Comment = {
  id: number;
  task_id: number;
  body: string;
  author_id: number;
  created_at: string;
};
export type NoteSummary = {
  id: number;
  title: string;
  archived: boolean;
  revision: number;
  created_by: number;
  created_at: string;
  updated_at: string;
};
export type Note = NoteSummary & { body: string };
export type Conversation = {
  id: number;
  kind: "channel" | "dm";
  name: string;
  peer: User | null;
  unread_count: number;
};
export type Message = {
  id: number;
  conversation_id: number;
  author_id: number;
  body: string;
  deleted: boolean;
  edited_at: string | null;
  created_at: string;
};
