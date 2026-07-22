// Talks to the local inkpages review UI (127.0.0.1:8322 by default). All
// fetches happen here — host_permissions let the worker reach localhost
// without CORS, and content scripts stay network-free.
import type { XtagInfo } from "../core/x";

interface Settings {
  baseUrl: string;
  token: string;
}

const DEFAULT_SETTINGS: Settings = { baseUrl: "http://127.0.0.1:8322", token: "" };
const STATUS_TTL_MS = 60_000;
const STATUS_CHUNK = 200;

const statusCache = new Map<string, { info: XtagInfo; ts: number }>();

async function getSettings(): Promise<Settings> {
  const stored = await chrome.storage.local.get(["baseUrl", "token"]);
  return {
    baseUrl: (stored.baseUrl as string) || DEFAULT_SETTINGS.baseUrl,
    token: (stored.token as string) || "",
  };
}

async function api(path: string, body?: unknown): Promise<any> {
  const { baseUrl, token } = await getSettings();
  if (!token) throw new Error("no API token set — open the x-tag popup");
  const resp = await fetch(baseUrl.replace(/\/$/, "") + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-Inkpages-Token": token,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (resp.status === 403) throw new Error("token rejected — check the x-tag popup");
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      detail = (await resp.json()).error ?? detail;
    } catch { /* not json */ }
    throw new Error(detail);
  }
  return resp.json();
}

function cacheAccounts(accounts: Record<string, XtagInfo>): void {
  const now = Date.now();
  for (const [handle, info] of Object.entries(accounts)) {
    statusCache.set(handle, { info, ts: now });
  }
}

async function handleStatus(handles: string[]): Promise<Record<string, XtagInfo>> {
  const now = Date.now();
  const result: Record<string, XtagInfo> = {};
  const misses: string[] = [];
  for (const handle of handles) {
    const hit = statusCache.get(handle);
    if (hit && now - hit.ts < STATUS_TTL_MS) {
      result[handle] = hit.info;
    } else {
      misses.push(handle);
    }
  }
  for (let i = 0; i < misses.length; i += STATUS_CHUNK) {
    const chunk = misses.slice(i, i + STATUS_CHUNK);
    const page = await api("/api/x/status", { handles: chunk });
    cacheAccounts(page.accounts);
    Object.assign(result, page.accounts);
  }
  return result;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    try {
      switch (message?.type) {
        case "XTAG_STATUS": {
          const accounts = await handleStatus(message.handles ?? []);
          sendResponse({ ok: true, accounts });
          break;
        }
        case "XTAG_TAG": {
          const page = await api("/api/x/tag", {
            handles: message.handles ?? [],
            referrer: message.referrer ?? null,
          });
          cacheAccounts(page.accounts);
          sendResponse({ ok: true, accounts: page.accounts });
          break;
        }
        case "XTAG_UNTAG": {
          const page = await api("/api/x/untag", { handles: message.handles ?? [] });
          cacheAccounts(page.accounts);
          sendResponse({ ok: true, accounts: page.accounts });
          break;
        }
        case "XTAG_QUEUE": {
          sendResponse({ ok: true, queue: await api("/api/x/queue") });
          break;
        }
        case "XTAG_FLUSH": {
          const result = await api("/api/x/flush", {
            run_pipeline: Boolean(message.runPipeline),
          });
          statusCache.clear(); // hydration changes states server-side
          sendResponse({ ok: true, result });
          break;
        }
        default:
          sendResponse({ ok: false, error: `unknown message ${message?.type}` });
      }
    } catch (error) {
      sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  })();
  return true; // async sendResponse
});
