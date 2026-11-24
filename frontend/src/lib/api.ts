// Thin fetch wrapper: cookie session, CSRF header, JSON errors surfaced as ApiError.
export class ApiError extends Error {
  status: number;
  data: any; // structured `detail` from the server, e.g. { message, video_id } on a duplicate import
  constructor(status: number, message: string, data: any = null) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

async function request<T>(method: string, url: string, body?: unknown, isForm = false): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "autocontent" };
  if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  const res = await fetch(url, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  if (res.status === 401 && !url.startsWith("/api/auth/")) {
    window.dispatchEvent(new CustomEvent("auth:expired"));
  }
  if (!res.ok) {
    let msg = res.statusText;
    let detail: any = null;
    try {
      const data = await res.json();
      detail = data.detail;
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail))
        msg = data.detail.map((d: any) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
      else if (data.detail && typeof data.detail.message === "string") msg = data.detail.message;
    } catch {
      /* not json */
    }
    if (res.status >= 500 && msg === res.statusText) msg = `Server error (${res.status}). Check Execution Logs.`;
    throw new ApiError(res.status, msg, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T = any>(url: string) => request<T>("GET", url),
  post: <T = any>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  put: <T = any>(url: string, body?: unknown) => request<T>("PUT", url, body ?? {}),
  patch: <T = any>(url: string, body?: unknown) => request<T>("PATCH", url, body ?? {}),
  del: <T = any>(url: string) => request<T>("DELETE", url),
  upload: <T = any>(url: string, form: FormData) => request<T>("POST", url, form, true),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>) {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}
