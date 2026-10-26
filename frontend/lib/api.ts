/**
 * Thin fetch wrapper for the CareerPilot API (same-origin via the Next.js /api rewrite).
 * - Sends the session cookie automatically and echoes the CSRF cookie in X-CSRF-Token.
 * - Normalizes the backend error envelope {error: {code, message, details}} into ApiError.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

type Options = {
  method?: string;
  body?: unknown;
  form?: FormData;
  query?: Record<string, string | number | boolean | string[] | undefined | null>;
  signal?: AbortSignal;
};

export function buildQuery(query?: Options["query"]): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => params.append(key, v));
    else params.set(key, String(value));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const method = (opts.method ?? (opts.body || opts.form ? "POST" : "GET")).toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (!SAFE_METHODS.has(method)) {
    const csrf = readCookie("cp_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}${buildQuery(opts.query)}`, {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: opts.signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "network_error", "Cannot reach the server. Check that the API is running.");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
    // Our API always answers with a JSON error envelope. A bare 5xx comes from the Next.js proxy
    // when the backend is down or unreachable.
    if (!err && res.status >= 500) {
      throw new ApiError(
        res.status,
        "backend_unreachable",
        "Cannot reach the CareerPilot API. Make sure the backend is running (uvicorn on port 8000).",
      );
    }
    throw new ApiError(
      res.status,
      err?.code ?? "http_error",
      err?.message ?? (res.status >= 500 ? "Something went wrong on the server." : `Request failed (${res.status})`),
      err?.details,
    );
  }
  return data as T;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "validation_error" && Array.isArray(err.details) && err.details.length) {
      const first = err.details[0] as { loc?: (string | number)[]; msg?: string };
      const field = first.loc?.filter((p) => p !== "body").join(".");
      return field ? `${field}: ${first.msg}` : (first.msg ?? err.message);
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return "Unexpected error";
}
