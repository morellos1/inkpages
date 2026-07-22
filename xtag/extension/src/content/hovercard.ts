// Tag button on the profile hover card — the primary flow: no need to visit
// the profile page, just hover a name anywhere and click.
import { parseHandleFromHref } from "../core/x";
import { createActionButton } from "./action-button";
import { subscribeToMutations } from "./observer";

const ROW_CLASS = "xtag-hovercard-row";

function findHoverCards(root: ParentNode): HTMLElement[] {
  const cards = new Set<HTMLElement>();
  if (root instanceof HTMLElement && root.matches('[data-testid="HoverCard"]')) {
    cards.add(root);
  }
  root.querySelectorAll<HTMLElement>('[data-testid="HoverCard"]').forEach((el) => cards.add(el));
  return [...cards];
}

function handleForCard(card: HTMLElement): string | null {
  const anchors = card.querySelectorAll<HTMLAnchorElement>("a[href]");
  for (const anchor of Array.from(anchors)) {
    const handle = parseHandleFromHref(anchor.getAttribute("href"));
    if (handle) return handle;
  }
  return null;
}

function decorate(card: HTMLElement): void {
  const existing = card.querySelector<HTMLElement>(`:scope .${ROW_CLASS}`);
  const handle = handleForCard(card);
  if (!handle) {
    existing?.remove();
    return;
  }
  if (existing) {
    if (existing.dataset.xtagHandle === handle) return;
    existing.remove(); // card got recycled for another user
  }
  const row = document.createElement("div");
  row.className = ROW_CLASS;
  row.dataset.xtagHandle = handle;
  row.appendChild(createActionButton(handle));
  // The card's inner scroll container is its first child div; appending to
  // the card itself keeps us clear of X's own layout logic.
  card.appendChild(row);
}

export function initHoverCard(): void {
  subscribeToMutations((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type !== "childList") continue;
      mutation.addedNodes.forEach((node) => {
        if (!(node instanceof HTMLElement)) return;
        findHoverCards(node).forEach(decorate);
      });
      // Card contents mutate in place when hovering user to user.
      if (mutation.target instanceof HTMLElement) {
        const card = mutation.target.closest<HTMLElement>('[data-testid="HoverCard"]');
        if (card) decorate(card);
      }
    }
  });
}
