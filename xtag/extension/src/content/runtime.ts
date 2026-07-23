// Content-side state store: batches status lookups through the service
// worker, keeps a per-page cache, and broadcasts updates so every feature
// (badges, hover card, profile button, list bar) re-renders together.
import type { XtagInfo } from "../core/x";

export const XTAG_UPDATED_EVENT = "inkpages:xtag-updated";

// Page-side cache with a TTL: server-side state changes on their own (a
// pipeline pass hydrates/lists accounts), so entries must expire or a long
// scrolling session shows stale badges until a full reload.
const CACHE_TTL_MS = 120_000;
const cache = new Map<string, { info: XtagInfo; ts: number }>();
let pending: Map<string, Array<(info: XtagInfo) => void>> | null = null;

function emitUpdated(): void {
  window.dispatchEvent(new CustomEvent(XTAG_UPDATED_EVENT));
}

async function send(message: Record<string, unknown>): Promise<any> {
  const response = await chrome.runtime.sendMessage(message);
  if (!response?.ok) throw new Error(response?.error ?? "xtag request failed");
  return response;
}

function cachePut(handle: string, info: XtagInfo): void {
  cache.set(handle, { info, ts: Date.now() });
}

export function cachedState(handle: string): XtagInfo | undefined {
  const hit = cache.get(handle);
  if (!hit || Date.now() - hit.ts > CACHE_TTL_MS) return undefined;
  return hit.info;
}

// Micro-batched: every caller in the same tick shares one XTAG_STATUS round
// trip (a timeline scan asks for dozens of handles one container at a time).
export function getState(handle: string): Promise<XtagInfo> {
  const hit = cachedState(handle);
  if (hit) return Promise.resolve(hit);
  return new Promise((resolve) => {
    if (!pending) {
      pending = new Map();
      setTimeout(async () => {
        const batch = pending!;
        pending = null;
        const handles = [...batch.keys()];
        try {
          const { accounts } = await send({ type: "XTAG_STATUS", handles });
          for (const [h, info] of Object.entries(accounts as Record<string, XtagInfo>)) {
            cachePut(h, info);
          }
        } catch (error) {
          console.warn("[xtag] status lookup failed:", error);
        }
        for (const [h, resolvers] of batch) {
          // A failed lookup resolves to "unknown", never "untracked": we don't
          // know the state, so features leave existing badges/buttons intact
          // rather than treating the handle as un-tagged. Not cached — the next
          // scan (scroll/mutation) retries, so it self-heals once the server is
          // back (e.g. after a pipeline pass finishes hammering the DB).
          const info = cachedState(h) ?? { state: "unknown" as const };
          resolvers.forEach((fn) => fn(info));
        }
      }, 80);
    }
    const list = pending.get(handle) ?? [];
    list.push(resolve);
    pending.set(handle, list);
  });
}

// Tag/untag never throw away partial progress: whatever the server confirmed
// is cached and returned even when a later chunk failed — callers keep the
// unconfirmed remainder (the bulk bar leaves it selected).
export interface WriteResult {
  accounts: Record<string, XtagInfo>;
  error?: string;
}

async function write(message: Record<string, unknown>): Promise<WriteResult> {
  const response = await chrome.runtime.sendMessage(message);
  const accounts = (response?.accounts ?? {}) as Record<string, XtagInfo>;
  for (const [h, info] of Object.entries(accounts)) {
    cachePut(h, info);
  }
  if (Object.keys(accounts).length > 0) emitUpdated();
  return { accounts, error: response?.ok ? undefined : (response?.error ?? "request failed") };
}

export function tagHandles(handles: string[]): Promise<WriteResult> {
  return write({ type: "XTAG_TAG", handles, referrer: location.href });
}

export function untagHandles(handles: string[]): Promise<WriteResult> {
  return write({ type: "XTAG_UNTAG", handles });
}

export function clearLocalCache(): void {
  cache.clear();
}
