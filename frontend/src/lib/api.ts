// Thin fetch wrapper: cookie session, CSRF header, JSON errors surfaced as ApiError.
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
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
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail))
        msg = data.detail.map((d: any) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
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
