// The one-click tag/untag button, shared by the hover card and the profile
// header. Self-syncing: renders from the runtime cache and re-renders on
// every xtag update event.
import type { XtagInfo } from "../core/x";
import { getState, tagHandles, untagHandles, XTAG_UPDATED_EVENT } from "./runtime";

const BTN_CLASS = "xtag-action-btn";

interface ButtonFace {
  label: string;
  title: string;
  className: string;
}

function faceFor(info: XtagInfo): ButtonFace {
  switch (info.state) {
    case "listed":
      return {
        label: "inked ✓",
        title: `In the inkpages directory${info.slug ? ` as /${info.slug}` : ""} — click to remove`,
        className: "xtag-btn-listed",
      };
    case "tagged":
      return {
        label: "inked ✓",
        title: `Tagged and hydrated (${info.detail ?? "lists on the next cluster run"}) — click to remove`,
        className: "xtag-btn-listed",
      };
    case "queued":
      return {
        label: "queued ✓",
        title: "Tagged — awaiting hydration; click to un-queue",
        className: "xtag-btn-queued",
      };
    case "removed":
      return {
        label: "re-add",
        title: `Removed from inkpages (${info.detail ?? "?"}) — click to re-add`,
        className: "xtag-btn-removed",
      };
    case "tracked":
      return {
        label: "+ ink",
        title: `Known to inkpages (${info.detail ?? "tracked"}) but not listed — click to tag as artist`,
        className: "xtag-btn-untracked",
      };
    default:
      return {
        label: "+ ink",
        title: "Tag this profile as an artist for inkpages",
        className: "xtag-btn-untracked",
      };
  }
}

export function createActionButton(handle: string, compact = false): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = BTN_CLASS + (compact ? " xtag-btn-compact" : "");
  button.dataset.xtagHandle = handle;
  button.textContent = "…";

  let current: XtagInfo = { state: "untracked" };

  const render = (info: XtagInfo): void => {
    // Server unreachable (e.g. mid-pipeline): keep the last-known face rather
    // than flipping a tagged profile back to "+ ink". The next successful sync
    // corrects it. current.state stays as-is so a click still acts on the last
    // known state (and tagging is idempotent server-side regardless).
    if (info.state === "unknown") return;
    current = info;
    const face = faceFor(info);
    button.textContent = face.label;
    button.title = face.title;
    button.className = `${BTN_CLASS} ${face.className}${compact ? " xtag-btn-compact" : ""}`;
  };

  const sync = (): void => {
    void getState(handle).then((info) => {
      if (button.isConnected) render(info);
    });
  };

  button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (button.disabled) return;
    button.disabled = true;
    try {
      let result;
      if (current.state === "listed") {
        const name = current.slug ? `@${handle} (artist /${current.slug})` : `@${handle}`;
        if (!window.confirm(
          `Remove ${name} from inkpages?\n\nThis suppresses the artist — it `
          + `disappears from the directory and re-discovery can never re-add it. `
          + `Reversible from the review UI's Removed page.`,
        )) {
          return;
        }
        result = await untagHandles([handle]);
      } else if (current.state === "queued" || current.state === "tagged") {
        result = await untagHandles([handle]);
      } else {
        result = await tagHandles([handle]);
        const note = result.accounts[handle]?.note;
        if (note) window.alert(`inkpages: ${note}`);
      }
      if (result.error) window.alert(`inkpages x-tag: ${result.error}`);
    } catch (error) {
      console.error("[xtag] action failed", error);
      window.alert(`inkpages x-tag: ${error instanceof Error ? error.message : error}`);
    } finally {
      button.disabled = false;
      sync();
    }
  });

  window.addEventListener(XTAG_UPDATED_EVENT, sync);
  sync();
  return button;
}
