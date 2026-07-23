// Compact ink button in every tweet's action row (reply/repost/like/…), so
// tagging works straight from the timeline with no hover needed.
//
// Author resolution: the article's first [data-testid="User-Name"] is always
// the ORIGINAL poster — on reposts X renders the reposter in a separate
// [data-testid="socialContext"] line (never a User-Name), and a quoted
// tweet's User-Name comes after the outer author's in DOM order. So reposts
// ink the original artist, exactly as intended.
import { findHandleInContainer } from "../core/x";
import { createActionButton } from "./action-button";
import { subscribeToMutations } from "./observer";

const HOLDER_CLASS = "xtag-post-action";
const SCAN_DEBOUNCE_MS = 200;

function authorHandle(article: HTMLElement): string | null {
  const userName = article.querySelector<HTMLElement>('[data-testid="User-Name"]');
  return userName ? findHandleInContainer(userName) : null;
}

function decorate(article: HTMLElement): void {
  const row = article.querySelector<HTMLElement>('[role="group"]');
  if (!row) return; // quoted-tweet cards have no action row — skipped
  const existing = row.querySelector<HTMLElement>(`:scope > .${HOLDER_CLASS}`);
  const handle = authorHandle(article);
  if (!handle) {
    existing?.remove();
    return;
  }
  if (existing) {
    if (existing.dataset.xtagHandle === handle) return;
    existing.remove(); // recycled article now shows another tweet
  }
  const holder = document.createElement("div");
  holder.className = HOLDER_CLASS;
  holder.dataset.xtagHandle = handle;
  holder.appendChild(createActionButton(handle, true));
  row.appendChild(holder);
}

function scan(root: ParentNode): void {
  const articles = new Set<HTMLElement>();
  if (root instanceof HTMLElement) {
    const closest = root.closest("article");
    if (closest) articles.add(closest);
  }
  root.querySelectorAll<HTMLElement>("article").forEach((a) => articles.add(a));
  articles.forEach(decorate);
}

export function initPostAction(): void {
  let debounceTimer: number | null = null;
  const pendingRoots = new Set<ParentNode>();

  const scheduleScan = (root: ParentNode): void => {
    pendingRoots.add(root);
    if (debounceTimer !== null) return;
    debounceTimer = window.setTimeout(() => {
      debounceTimer = null;
      const roots = pendingRoots.size > 24 ? [document] : [...pendingRoots];
      pendingRoots.clear();
      roots.forEach(scan);
    }, SCAN_DEBOUNCE_MS);
  };

  scheduleScan(document);
  subscribeToMutations((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type !== "childList") continue;
      mutation.addedNodes.forEach((node) => {
        if (node instanceof HTMLElement) scheduleScan(node);
      });
    }
  });
}
