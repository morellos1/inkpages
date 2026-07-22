// Content-side state store: batches status lookups through the service
// worker, keeps a per-page cache, and broadcasts updates so every feature
// (badges, hover card, profile button, list bar) re-renders together.
import type { XtagInfo } from "../core/x";

export const XTAG_UPDATED_EVENT = "inkpages:xtag-updated";

const cache = new Map<string, XtagInfo>();
let pending: Map<string, Array<(info: XtagInfo) => void>> | null = null;

function emitUpdated(): void {
  window.dispatchEvent(new CustomEvent(XTAG_UPDATED_EVENT));
}

async function send(message: Record<string, unknown>): Promise<any> {
  const response = await chrome.runtime.sendMessage(message);
  if (!response?.ok) throw new Error(response?.error ?? "xtag request failed");
  return response;
}

export function cachedState(handle: string): XtagInfo | undefined {
  return cache.get(handle);
}

// Micro-batched: every caller in the same tick shares one XTAG_STATUS round
// trip (a timeline scan asks for dozens of handles one container at a time).
export function getState(handle: string): Promise<XtagInfo> {
  const hit = cache.get(handle);
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
            cache.set(h, info);
          }
        } catch (error) {
          console.warn("[xtag] status lookup failed:", error);
        }
        for (const [h, resolvers] of batch) {
          const info = cache.get(h) ?? { state: "untracked" as const };
          resolvers.forEach((fn) => fn(info));
        }
      }, 80);
    }
    const list = pending.get(handle) ?? [];
    list.push(resolve);
    pending.set(handle, list);
  });
}

export async function tagHandles(handles: string[]): Promise<Record<string, XtagInfo>> {
  const { accounts } = await send({
    type: "XTAG_TAG", handles, referrer: location.href,
  });
  for (const [h, info] of Object.entries(accounts as Record<string, XtagInfo>)) {
    cache.set(h, info);
  }
  emitUpdated();
  return accounts;
}

export async function untagHandles(handles: string[]): Promise<Record<string, XtagInfo>> {
  const { accounts } = await send({ type: "XTAG_UNTAG", handles });
  for (const [h, info] of Object.entries(accounts as Record<string, XtagInfo>)) {
    cache.set(h, info);
  }
  emitUpdated();
  return accounts;
}

export function clearLocalCache(): void {
  cache.clear();
}
