// Route-independent memory cache. Data survives navigation, never a browser reload.
const registry = new Map();
let generation = 0;

export function getResourceEntry(key) {
  if (!registry.has(key)) {
    registry.set(key, { data: undefined, error: null, loading: true, listeners: new Set(), inFlight: null });
  }
  return registry.get(key);
}

function notify(entry) {
  entry.listeners.forEach((listener) => listener());
}

export function loadResource(key, fetcher) {
  const entry = getResourceEntry(key);
  if (entry.inFlight) return entry.inFlight;
  const startedGeneration = generation;
  const request = Promise.resolve().then(fetcher).then((data) => {
    if (startedGeneration === generation && registry.get(key) === entry) {
      entry.data = data;
      entry.error = null;
      entry.loading = false;
      notify(entry);
    }
    return data;
  }, (error) => {
    if (startedGeneration === generation && registry.get(key) === entry) {
      entry.error = error;
      entry.loading = false;
      notify(entry);
    }
    throw error;
  }).finally(() => {
    if (entry.inFlight === request) entry.inFlight = null;
  });
  entry.inFlight = request;
  return request;
}

// Writes must be followed by a fresh request, even if an earlier read is pending.
export function reloadResource(key, fetcher) {
  const entry = getResourceEntry(key);
  if (!entry.inFlight) return loadResource(key, fetcher);
  return entry.inFlight.catch(() => undefined).then(() => loadResource(key, fetcher));
}

export function clearResourceCache() {
  generation += 1;
  const entries = [...registry.values()];
  registry.clear();
  entries.forEach((entry) => {
    globalThis.clearInterval(entry.timerId);
    entry.consumers?.clear();
    entry.revalidate = null;
    entry.data = undefined;
    entry.error = null;
    entry.loading = true;
    notify(entry);
  });
}

export function invalidateResourceCache() {
  // Keep the visible result during refresh; unused results must be fetched again.
  for (const [key, entry] of registry) {
    if (!entry.listeners.size) registry.delete(key);
    else entry.revalidate?.();
  }
}
