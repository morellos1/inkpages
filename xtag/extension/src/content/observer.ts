// Single shared MutationObserver fanning batches out to subscribers —
// lifted from better-x (wongtp/better-x, same author). X mutates the body
// constantly; N features must not mean N tree walks per change.

type MutationHandler = (mutations: MutationRecord[]) => void;

interface SubscribeOptions {
  childList?: boolean;
  attributeFilter?: string[];
}

interface Subscriber {
  handler: MutationHandler;
  childList: boolean;
  attributeFilter: Set<string> | null;
}

const subscribers: Subscriber[] = [];
let observer: MutationObserver | null = null;

function isRelevant(subscriber: Subscriber, mutation: MutationRecord): boolean {
  if (mutation.type === "childList") return subscriber.childList;
  if (mutation.type === "attributes") {
    return subscriber.attributeFilter !== null
      && subscriber.attributeFilter.has(mutation.attributeName ?? "");
  }
  return false;
}

function dispatch(mutations: MutationRecord[]): void {
  for (const subscriber of subscribers) {
    const subset = mutations.filter((mutation) => isRelevant(subscriber, mutation));
    if (subset.length > 0) subscriber.handler(subset);
  }
}

function reconnect(): void {
  const observedAttributes = new Set<string>();
  for (const subscriber of subscribers) {
    if (subscriber.attributeFilter) {
      for (const name of subscriber.attributeFilter) observedAttributes.add(name);
    }
  }
  const watchAttributes = observedAttributes.size > 0;
  observer ??= new MutationObserver(dispatch);
  observer.disconnect();
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: watchAttributes,
    ...(watchAttributes ? { attributeFilter: [...observedAttributes] } : {}),
  });
}

export function subscribeToMutations(
  handler: MutationHandler,
  options: SubscribeOptions = {},
): void {
  subscribers.push({
    handler,
    childList: options.childList ?? true,
    attributeFilter: options.attributeFilter ? new Set(options.attributeFilter) : null,
  });
  reconnect();
}
