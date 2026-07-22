// X DOM/URL parsing shared by every content feature. Adapted from better-x
// (wongtp/better-x, same author) — the handle-parsing rules there survived a
// lot of real-world X markup churn, keep them in sync when X breaks things.

export type XtagState = "untracked" | "queued" | "tracked" | "listed" | "removed";

export interface XtagInfo {
  state: XtagState;
  detail?: string | null;
  artist_id?: number | null;
  slug?: string | null;
  note?: string;
}

const RESERVED_PATH_SEGMENTS = new Set([
  "home", "explore", "notifications", "messages", "i", "search", "compose",
  "settings", "tos", "privacy", "about", "help", "logout", "login", "signup",
  "intent", "hashtag", "share",
]);

export function normalizeHandle(value: string | undefined | null): string | null {
  if (!value) return null;
  const trimmed = value.trim().replace(/^@+/, "");
  const normalized = trimmed.toLowerCase().replace(/[^a-z0-9_]/g, "");
  if (!normalized || normalized.length > 15) return null;
  return normalized;
}

export function parseHandleFromHref(rawHref: string | null): string | null {
  if (!rawHref) return null;
  const href = rawHref.trim();
  if (/\/status\/\d+/i.test(href)) return null;
  const match = href.match(/^(?:https?:\/\/(?:x\.com|twitter\.com))?\/([A-Za-z0-9_]{1,15})(?:\/|$)/i);
  if (!match) return null;
  const handle = normalizeHandle(match[1]);
  if (!handle || RESERVED_PATH_SEGMENTS.has(handle)) return null;
  return handle;
}

export function parseProfileHandleFromPathname(pathname: string): string | null {
  const match = pathname.match(/^\/([A-Za-z0-9_]{1,15})(?:\/|$)/);
  if (!match) return null;
  const handle = normalizeHandle(match[1]);
  if (!handle || RESERVED_PATH_SEGMENTS.has(handle)) return null;
  return handle;
}

// Connection/relationship list tabs under a profile (followers/following etc.)
export function isConnectionsListPathname(pathname: string): boolean {
  return /\/(?:followers|following|verified_followers|followers_you_follow|followers_you_know|creator-subscriptions|subscriptions)(?:\/|$)/.test(
    pathname,
  );
}

// Find the @handle a UserCell / User-Name container is about.
export function findHandleInContainer(container: HTMLElement): string | null {
  const spans = container.querySelectorAll<HTMLElement>(
    '[data-testid="User-Name"] span, [data-testid="UserName"] span, [data-testid="UserCell"] span, span[dir="ltr"]',
  );
  for (const node of Array.from(spans)) {
    const text = node.textContent?.trim() ?? "";
    const match = text.match(/^@([A-Za-z0-9_]{1,15})$/);
    if (match) return normalizeHandle(match[1]);
  }
  const anchors = container.querySelectorAll<HTMLAnchorElement>("a[href]");
  for (const anchor of Array.from(anchors)) {
    const handle = parseHandleFromHref(anchor.getAttribute("href"));
    if (handle) return handle;
  }
  return null;
}
