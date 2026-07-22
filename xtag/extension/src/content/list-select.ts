// Bulk flow for follower/following lists: a checkbox on every user row, a
// sticky bottom bar with Select all / Add / Remove. Selection is keyed by
// handle (X virtualizes rows — DOM nodes recycle as you scroll), so checked
// state survives scrolling and "select all" accumulates across screenfuls.
import { findHandleInContainer, isConnectionsListPathname } from "../core/x";
import { tagHandles, untagHandles } from "./runtime";
import { subscribeToMutations } from "./observer";

const CHECKBOX_CLASS = "xtag-row-check";
const BAR_ID = "xtag-list-bar";
const SCAN_DEBOUNCE_MS = 150;

const selected = new Set<string>();
let barBusy = false;

function findUserCells(root: ParentNode): HTMLElement[] {
  const cells = new Set<HTMLElement>();
  if (root instanceof HTMLElement && root.matches('[data-testid="UserCell"]')) {
    cells.add(root);
  }
  root.querySelectorAll<HTMLElement>('[data-testid="UserCell"]').forEach((el) => cells.add(el));
  return [...cells];
}

function decorateCell(cell: HTMLElement): void {
  const handle = findHandleInContainer(cell);
  if (!handle) return;
  let box = cell.querySelector<HTMLInputElement>(`:scope > .${CHECKBOX_CLASS}`);
  if (!box) {
    box = document.createElement("input");
    box.type = "checkbox";
    box.className = CHECKBOX_CLASS;
    box.addEventListener("click", (event) => event.stopPropagation());
    box.addEventListener("change", () => {
      const h = box!.dataset.xtagHandle;
      if (!h) return;
      if (box!.checked) selected.add(h);
      else selected.delete(h);
      renderBar();
    });
    cell.style.position = "relative";
    cell.appendChild(box);
  }
  box.dataset.xtagHandle = handle;
  box.checked = selected.has(handle);
}

function visibleHandles(): string[] {
  const handles: string[] = [];
  document.querySelectorAll<HTMLInputElement>(`.${CHECKBOX_CLASS}`).forEach((box) => {
    if (box.dataset.xtagHandle) handles.push(box.dataset.xtagHandle);
  });
  return handles;
}

function syncCheckboxes(): void {
  document.querySelectorAll<HTMLInputElement>(`.${CHECKBOX_CLASS}`).forEach((box) => {
    const h = box.dataset.xtagHandle;
    if (h) box.checked = selected.has(h);
  });
}

function ensureBar(): HTMLElement {
  let bar = document.getElementById(BAR_ID);
  if (bar) return bar;
  bar = document.createElement("div");
  bar.id = BAR_ID;
  bar.innerHTML = `
    <span class="xtag-bar-count"></span>
    <button type="button" data-act="all">Select all</button>
    <button type="button" data-act="clear">Clear</button>
    <button type="button" data-act="add" class="xtag-bar-add">Add to inkpages</button>
    <button type="button" data-act="remove" class="xtag-bar-remove">Remove</button>
    <span class="xtag-bar-msg"></span>`;
  bar.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-act]");
    if (button) void onBarAction(button.dataset.act!);
  });
  document.body.appendChild(bar);
  return bar;
}

function renderBar(): void {
  const bar = ensureBar();
  bar.style.display = isConnectionsListPathname(location.pathname) ? "flex" : "none";
  bar.querySelector(".xtag-bar-count")!.textContent =
    selected.size ? `${selected.size} selected` : "none selected";
  bar.querySelectorAll("button").forEach((b) => {
    b.disabled = barBusy || (b.dataset.act !== "all" && selected.size === 0);
    if (b.dataset.act === "all") b.disabled = barBusy;
  });
}

function setMessage(text: string): void {
  const msg = document.getElementById(BAR_ID)?.querySelector(".xtag-bar-msg");
  if (msg) msg.textContent = text;
}

async function onBarAction(action: string): Promise<void> {
  if (barBusy) return;
  if (action === "all") {
    visibleHandles().forEach((h) => selected.add(h));
    syncCheckboxes();
    renderBar();
    return;
  }
  if (action === "clear") {
    selected.clear();
    syncCheckboxes();
    renderBar();
    return;
  }
  const handles = [...selected];
  if (handles.length === 0) return;
  if (action === "remove" && !window.confirm(
    `Remove ${handles.length} profile(s) from inkpages? Listed artists get `
    + `suppressed (reversible from the review UI's Removed page).`,
  )) {
    return;
  }
  barBusy = true;
  renderBar();
  setMessage("working…");
  try {
    const accounts = action === "add"
      ? await tagHandles(handles)
      : await untagHandles(handles);
    const queued = Object.values(accounts).filter((a) => a.state === "queued").length;
    setMessage(action === "add"
      ? `done — ${queued} queued for hydration`
      : "done");
    selected.clear();
    syncCheckboxes();
  } catch (error) {
    setMessage(`failed: ${error instanceof Error ? error.message : error}`);
  } finally {
    barBusy = false;
    renderBar();
  }
}

export function initListSelect(): void {
  let debounceTimer: number | null = null;
  let lastPathname = location.pathname;
  const pendingRoots = new Set<ParentNode>();

  const scan = (): void => {
    if (!isConnectionsListPathname(location.pathname)) {
      renderBar(); // hides it
      return;
    }
    const roots = pendingRoots.size ? [...pendingRoots] : [document];
    pendingRoots.clear();
    roots.forEach((root) => findUserCells(root).forEach(decorateCell));
    renderBar();
  };

  const scheduleScan = (root: ParentNode): void => {
    pendingRoots.add(root);
    if (debounceTimer !== null) return;
    debounceTimer = window.setTimeout(() => {
      debounceTimer = null;
      scan();
    }, SCAN_DEBOUNCE_MS);
  };

  scheduleScan(document);
  subscribeToMutations((mutations) => {
    if (location.pathname !== lastPathname) {
      lastPathname = location.pathname;
      // Leaving/entering a list: keep the selection (it's cross-page useful
      // when flipping followers <-> following), just re-render.
      scheduleScan(document);
      return;
    }
    if (!isConnectionsListPathname(location.pathname)) return;
    for (const mutation of mutations) {
      if (mutation.type === "childList") {
        mutation.addedNodes.forEach((node) => {
          if (node instanceof HTMLElement) scheduleScan(node);
        });
      }
    }
  });
}
