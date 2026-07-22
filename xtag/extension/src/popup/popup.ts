const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

const baseUrlInput = $<HTMLInputElement>("base-url");
const tokenInput = $<HTMLInputElement>("token");
const settingsMsg = $("settings-msg");
const queueCount = $("queue-count");
const budgetLine = $("budget-line");
const flushButton = $<HTMLButtonElement>("flush");
const flushMsg = $("flush-msg");
const runPipeline = $<HTMLInputElement>("run-pipeline");

const dollars = (cents: number): string => `$${(cents / 100).toFixed(2)}`;

async function refreshQueue(): Promise<void> {
  const response = await chrome.runtime.sendMessage({ type: "XTAG_QUEUE" });
  if (!response?.ok) {
    queueCount.textContent = "–";
    budgetLine.textContent = response?.error ?? "cannot reach server";
    flushButton.disabled = true;
    return;
  }
  const { queued, est_cents, spent_cents, cap_cents } = response.queue;
  queueCount.textContent = String(queued);
  budgetLine.textContent =
    `hydration cost ~${dollars(est_cents)} · X spend ${dollars(spent_cents)} of ${dollars(cap_cents)}`;
  flushButton.disabled = queued === 0;
  flushButton.textContent = queued ? `Hydrate now (~${dollars(est_cents)})` : "Hydrate now";
}

$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    baseUrl: baseUrlInput.value.trim() || "http://127.0.0.1:8322",
    token: tokenInput.value.trim(),
  });
  settingsMsg.textContent = "testing…";
  const response = await chrome.runtime.sendMessage({ type: "XTAG_QUEUE" });
  settingsMsg.textContent = response?.ok ? "connected ✓" : `failed: ${response?.error}`;
  void refreshQueue();
});

flushButton.addEventListener("click", async () => {
  flushButton.disabled = true;
  flushMsg.textContent = "hydrating…";
  const response = await chrome.runtime.sendMessage({
    type: "XTAG_FLUSH",
    runPipeline: runPipeline.checked,
  });
  if (response?.ok) {
    const { hydrated, missing, cost_cents, pipeline_started } = response.result;
    flushMsg.textContent =
      `hydrated ${hydrated} (${dollars(cost_cents)})`
      + (missing ? `, ${missing} gone from X` : "")
      + (pipeline_started ? " — pipeline running, artists list when it finishes" : "");
  } else {
    flushMsg.textContent = `failed: ${response?.error}`;
  }
  void refreshQueue();
});

(async () => {
  const stored = await chrome.storage.local.get(["baseUrl", "token"]);
  baseUrlInput.value = (stored.baseUrl as string) ?? "";
  tokenInput.value = (stored.token as string) ?? "";
  void refreshQueue();
})();
