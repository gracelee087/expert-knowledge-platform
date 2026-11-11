export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const WS_URL = API_URL.replace(/^http/, "ws");

export type Role = "admin" | "interviewer" | "expert";

export function getIdentity(): { role: Role; user: string } {
  if (typeof window === "undefined") return { role: "admin", user: "demo-admin" };
  const role = (localStorage.getItem("ekep.role") as Role) || "admin";
  const user = localStorage.getItem("ekep.user") || `demo-${role}`;
  return { role, user };
}

export function setIdentity(role: Role, user: string) {
  localStorage.setItem("ekep.role", role);
  localStorage.setItem("ekep.user", user);
  window.dispatchEvent(new Event("ekep-identity"));
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function headers(extra?: HeadersInit): HeadersInit {
  const { role, user } = getIdentity();
  return { "X-Demo-Role": role, "X-Demo-User": user, ...(extra || {}) };
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : res.blob()) as Promise<T>;
}

export const api = {
  get: <T,>(path: string) => fetch(`${API_URL}${path}`, { headers: headers(), cache: "no-store" }).then(handle<T>),
  post: <T,>(path: string, body?: unknown) =>
    fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(handle<T>),
  put: <T,>(path: string, body: unknown) =>
    fetch(`${API_URL}${path}`, { method: "PUT", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body) }).then(handle<T>),
  del: (path: string) => fetch(`${API_URL}${path}`, { method: "DELETE", headers: headers() }).then(handle<void>),
  upload: <T,>(path: string, form: FormData) =>
    fetch(`${API_URL}${path}`, { method: "POST", headers: headers(), body: form }).then(handle<T>),
  blob: (path: string, body: unknown) =>
    fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then(handle<Blob>),
};

export function fmtTime(s: number | null | undefined) {
  if (s == null) return "–";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export function fmtDate(iso: string | null | undefined) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
