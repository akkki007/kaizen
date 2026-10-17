// URL-backed filter state without lost updates: router.replace commits asynchronously, so an
// update built from the last rendered search params can overwrite one that is still in flight
// (click a filter, then the debounced search fires before the URL caught up). Updates are queued
// and each builds on the latest requested query; the queue drains as the URL catches up.

export type ParamPatch = Record<string, string | null>;

/** Apply a patch to a query string (null or "" deletes the key). */
export function applyPatch(base: string, patch: ParamPatch): string {
  const next = new URLSearchParams(base);
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === "") next.delete(k);
    else next.set(k, v);
  }
  return next.toString();
}

/** Drop queued queries the URL has reached. If the URL matches none of them, it changed from
 * elsewhere (back/forward, a link), so the queue is stale and is cleared. */
export function settle(queue: readonly string[], current: string): string[] {
  const i = queue.indexOf(current);
  return i === -1 ? [] : queue.slice(i + 1);
}

/** The query the next update should build on: the newest one still in flight, else the URL. */
export function baseQuery(queue: readonly string[], current: string): string {
  return queue.length ? queue[queue.length - 1] : current;
}
