import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";
import { mockFetch } from "./mock";

export const MOCK = process.env.NEXT_PUBLIC_API_MOCK === "1";

function cookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith(`${name}=`))
    ?.split("=")[1];
}

// Cookie-authenticated, state-changing requests carry the double-submit CSRF token.
const csrf: Middleware = {
  onRequest({ request }) {
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      const token = cookie("cd_csrf");
      if (token) request.headers.set("x-csrf-token", token);
    }
    return request;
  },
};

export const api = createClient<paths>({
  baseUrl: "",
  credentials: "include",
  fetch: MOCK ? mockFetch : undefined,
});
api.use(csrf);

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public requestId?: string,
  ) {
    super(message);
  }
}

/** Unwraps an openapi-fetch result: data on success, a typed ApiError otherwise. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined) return result.data;
  const body = result.error as { error?: { message?: string; request_id?: string } } | undefined;
  throw new ApiError(
    result.response.status,
    body?.error?.message ?? result.response.statusText ?? "request failed",
    body?.error?.request_id,
  );
}
