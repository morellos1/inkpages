// "Already tagged" indication while scrolling: a small pill next to names
// everywhere (timeline, user cells, profile headers). Placement strategy
// adapted from better-x's ai-name-badges (same author).
import { findHandleInContainer, type XtagInfo, type XtagState } from "../core/x";
import { cachedState, getState, XTAG_UPDATED_EVENT } from "./runtime";
import { subscribeToMutations } from "./observer";

const BADGE_CLASS = "xtag-badge";
const SCAN_DEBOUNCE_MS = 150;
const MAX_PENDING_ROOTS = 64;

const BADGE_TEXT: Partial<Record<XtagState, string>> = {
  listed: "INK",
  tagged: "INK",
  queued: "INK…",
  removed: "✕",
};

const BADGE_TITLE: Partial<Record<XtagState, string>> = {
  listed: "In the inkpages directory",
  tagged: "Hydrated — lists on the next cluster run",
  queued: "Tagged for inkpages — awaiting hydration",
  removed: "Removed from inkpages",
};

function makeBadge(state: XtagState, info: XtagInfo): HTMLSpanElement {
  const badge = document.createElement("span");
  badge.className = `${BADGE_CLASS} xtag-badge-${state}`;
  badge.textContent = BADGE_TEXT[state] ?? "";
  badge.title = (BADGE_TITLE[state] ?? "") + (info.detail ? ` (${info.detail})` : "");
  badge.dataset.xtagState = state;
  return badge;
}

function getCandidateContainers(root: ParentNode): HTMLElement[] {
  const selectors = [
    '[data-testid="User-Name"]',
    '[data-testid="UserName"]',
    '[data-testid="UserCell"]',
  ];
  const candidates = new Set<HTMLElement>();
  if (root instanceof HTMLElement) {
    for (const selector of selectors) {
      if (root.matches(selector)) candidates.add(root);
    }
  }
  root.querySelectorAll<HTMLElement>(selectors.join(", ")).forEach((el) => {
    // Hover cards get their own richer UI (hovercard.ts).
    if (el.closest('[data-testid="HoverCard"], [role="tooltip"]')) return;
    // Nested User-Name inside a UserCell: badge the cell once.
    if (el.dataset.testid !== "UserCell" && el.closest('[data-testid="UserCell"]')) return;
    candidates.add(el);
  });
  return [...candidates];
}

function findNameAnchor(container: HTMLElement): HTMLElement | null {
  // Right after the @handle span reads cleanly in every layout X ships.
  const spans = container.querySelectorAll<HTMLElement>("span");
  for (const node of Array.from(spans)) {
    if (/^@[A-Za-z0-9_]{1,15}$/.test(node.textContent?.trim() ?? "")) {
      return node;
    }
  }
  return null;
}

function applyBadge(container: HTMLElement, info: XtagInfo): void {
  const existing = container.querySelector<HTMLElement>(`:scope .${BADGE_CLASS}`);
  const text = BADGE_TEXT[info.state];
  if (!text) {
    existing?.remove();
    return;
  }
  if (existing && existing.dataset.xtagState === info.state) return;
  existing?.remove();
  const anchor = findNameAnchor(container);
  const badge = makeBadge(info.state, info);
  if (anchor) {
    anchor.insertAdjacentElement("afterend", badge);
  } else {
    container.appendChild(badge);
  }
}

async function processRoot(root: ParentNode): Promise<void> {
  const containers = getCandidateContainers(root);
  await Promise.all(containers.map(async (container) => {
    const handle = findHandleInContainer(container);
    if (!handle) return;
    const info = cachedState(handle) ?? await getState(handle);
    if (!container.isConnected) return;
    // Re-check identity: virtualized rows get recycled while we await.
    if (findHandleInContainer(container) !== handle) return;
    applyBadge(container, info);
  }));
}

export function initBadges(): void {
  let debounceTimer: number | null = null;
  let lastPathname = location.pathname;
  const pendingRoots = new Set<ParentNode>();

  const addPendingRoot = (root: ParentNode): void => {
    if (root === document || pendingRoots.size >= MAX_PENDING_ROOTS) {
      pendingRoots.clear();
      pendingRoots.add(document);
      return;
    }
    if (pendingRoots.has(document)) return;
    if (root instanceof Node) {
      for (const existing of Array.from(pendingRoots)) {
        if (existing instanceof Node) {
          if (existing.contains(root)) return;
          if (root.contains(existing)) pendingRoots.delete(existing);
        }
      }
    }
    pendingRoots.add(root);
  };

  const scheduleScan = (root: ParentNode): void => {
    addPendingRoot(root);
    if (debounceTimer !== null) return;
    debounceTimer = window.setTimeout(() => {
      debounceTimer = null;
      const roots = [...pendingRoots];
      pendingRoots.clear();
      roots.forEach((r) => void processRoot(r));
    }, SCAN_DEBOUNCE_MS);
  };

  void processRoot(document);
  window.addEventListener(XTAG_UPDATED_EVENT, () => scheduleScan(document));

  subscribeToMutations((mutations) => {
    if (location.pathname !== lastPathname) {
      lastPathname = location.pathname;
      document.querySelectorAll(`.${BADGE_CLASS}`).forEach((n) => n.remove());
      scheduleScan(document);
      return;
    }
    for (const mutation of mutations) {
      if (mutation.type === "childList") {
        mutation.addedNodes.forEach((node) => {
          if (node instanceof HTMLElement) scheduleScan(node);
        });
      }
    }
  });
}
