import { beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { clearResourceCache, getResourceEntry, invalidateResourceCache, loadResource, reloadResource } from "./resourceCache.js";

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
beforeEach(clearResourceCache);

test("navigation can read cached data while a slow refresh is pending", async () => {
  await loadResource("company:1:period:A", async () => ({ count: 15 }));
  const pending = deferred();
  const refresh = loadResource("company:1:period:A", () => pending.promise);
  assert.deepEqual(getResourceEntry("company:1:period:A").data, { count: 15 });
  assert.equal(getResourceEntry("company:1:period:A").loading, false);
  pending.resolve({ count: 16 });
  await refresh;
  assert.deepEqual(getResourceEntry("company:1:period:A").data, { count: 16 });
});

test("concurrent reads of the same query share a request; different queries stay separate", async () => {
  const pending = deferred();
  let calls = 0;
  const fetcher = () => { calls++; return pending.promise; };
  const first = loadResource("company:1:page:1", fetcher);
  assert.equal(loadResource("company:1:page:1", fetcher), first);
  await loadResource("company:1:page:2", async () => [2]);
  await loadResource("company:2:page:1", async () => [3]);
  pending.resolve([1]);
  await first;
  assert.equal(calls, 1);
  assert.deepEqual(getResourceEntry("company:1:page:1").data, [1]);
  assert.deepEqual(getResourceEntry("company:1:page:2").data, [2]);
  assert.deepEqual(getResourceEntry("company:2:page:1").data, [3]);
});

test("refresh failure preserves the last successful result, and the next read recovers", async () => {
  await loadResource("risks", async () => [15]);
  await assert.rejects(loadResource("risks", () => { throw new Error("offline"); }));
  assert.deepEqual(getResourceEntry("risks").data, [15]);
  assert.equal(getResourceEntry("risks").loading, false);
  await loadResource("risks", async () => [16]);
  assert.equal(getResourceEntry("risks").error, null);
});

test("explicit reload after a write runs after the pending read", async () => {
  const pending = deferred();
  const oldRead = loadResource("risks", () => pending.promise);
  let calls = 0;
  const freshRead = reloadResource("risks", async () => { calls++; return [16]; });
  assert.equal(calls, 0);
  pending.resolve([15]);
  await Promise.all([oldRead, freshRead]);
  assert.equal(calls, 1);
  assert.deepEqual(getResourceEntry("risks").data, [16]);
});

test("logout discards snapshots and late responses from the previous session", async () => {
  const pending = deferred();
  await loadResource("companies", async () => ["old account"]);
  const oldRead = loadResource("companies", () => pending.promise);
  clearResourceCache();
  assert.equal(getResourceEntry("companies").data, undefined);
  await loadResource("companies", async () => ["new account"]);
  pending.resolve(["late old account"]);
  await oldRead;
  assert.deepEqual(getResourceEntry("companies").data, ["new account"]);
});

test("invalidation refreshes visible queries and drops unused snapshots and pending results", async () => {
  await loadResource("active", async () => [1]);
  const entry = getResourceEntry("active");
  entry.listeners.add(() => {});
  let refreshed = false;
  entry.revalidate = () => { refreshed = true; };
  await loadResource("inactive", async () => [2]);
  const pending = deferred();
  const oldRead = loadResource("inactive", () => pending.promise);
  invalidateResourceCache();
  assert.equal(refreshed, true);
  assert.deepEqual(entry.data, [1]);
  assert.equal(getResourceEntry("inactive").data, undefined);
  pending.resolve([3]);
  await oldRead;
  assert.equal(getResourceEntry("inactive").data, undefined);
});
