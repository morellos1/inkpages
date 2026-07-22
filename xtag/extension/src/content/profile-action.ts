// Tag button in the profile header's action cluster (next to Follow/More).
// Anchoring strategy lifted from better-x's ai-profile-action (same author):
// gate on the header "More" button, which never exists on connection lists.
import { isConnectionsListPathname, parseProfileHandleFromPathname } from "../core/x";
import { createActionButton } from "./action-button";
import { subscribeToMutations } from "./observer";

const WRAPPER_ID = "xtag-profile-action";
const SCAN_DEBOUNCE_MS = 150;
const MAX_SCROLL_Y = 120;

function removeWrapper(): void {
  document.getElementById(WRAPPER_ID)?.remove();
}

function isHeaderActionButton(node: HTMLElement): boolean {
  if (node.closest("article")) return false;
  if (node.closest('[data-testid="UserCell"], [data-testid="cellInnerDiv"], [role="listitem"]')) {
    return false;
  }
  return true;
}

function findHost(): HTMLElement | null {
  if (isConnectionsListPathname(location.pathname)) return null;
  const root = document.querySelector('[data-testid="primaryColumn"]');
  if (!(root instanceof HTMLElement)) return null;
  const moreButtons = root.querySelectorAll<HTMLElement>('[data-testid="userActions"]');
  for (const button of Array.from(moreButtons)) {
    if (isHeaderActionButton(button)) return button;
  }
  return null;
}

export function initProfileAction(): void {
  let debounceTimer: number | null = null;

  const scheduleScan = (): void => {
    if (debounceTimer !== null) return;
    debounceTimer = window.setTimeout(() => {
      debounceTimer = null;
      if (window.scrollY > MAX_SCROLL_Y || isConnectionsListPathname(location.pathname)) {
        removeWrapper();
        return;
      }
      const handle = parseProfileHandleFromPathname(location.pathname);
      const host = handle ? findHost() : null;
      if (!handle || !host) {
        removeWrapper();
        return;
      }
      const existing = document.getElementById(WRAPPER_ID);
      if (existing?.dataset.xtagHandle === handle && existing.isConnected) return;
      removeWrapper();
      const wrapper = document.createElement("div");
      wrapper.id = WRAPPER_ID;
      wrapper.className = "xtag-profile-action";
      wrapper.dataset.xtagHandle = handle;
      wrapper.appendChild(createActionButton(handle));
      // Sit immediately left of the "More" button in the action cluster.
      host.parentElement?.insertBefore(wrapper, host);
    }, SCAN_DEBOUNCE_MS);
  };

  scheduleScan();
  window.addEventListener("scroll", scheduleScan, { passive: true });
  window.addEventListener("popstate", scheduleScan);

  let cachedPrimaryColumn: HTMLElement | null = null;
  const getPrimaryColumn = (): HTMLElement | null => {
    if (cachedPrimaryColumn?.isConnected) return cachedPrimaryColumn;
    const found = document.querySelector('[data-testid="primaryColumn"]');
    cachedPrimaryColumn = found instanceof HTMLElement ? found : null;
    return cachedPrimaryColumn;
  };

  subscribeToMutations((mutations) => {
    const primaryColumn = getPrimaryColumn();
    if (!primaryColumn) return;
    const relevant = mutations.some((mutation) =>
      primaryColumn.contains(mutation.target)
      || Array.from(mutation.addedNodes).some(
        (node) => node instanceof HTMLElement && primaryColumn.contains(node),
      ));
    if (relevant) scheduleScan();
  });
}
