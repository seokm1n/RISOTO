import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { getResourceEntry, loadResource, reloadResource } from "./resourceCache";

function configurePolling(key, entry) {
  window.clearInterval(entry.timerId);
  const consumers = [...entry.consumers.values()];
  const run = (load) => {
    const consumer = [...entry.consumers.values()][0];
    return consumer ? load(key, consumer.fetcher).catch(() => undefined) : Promise.resolve();
  };
  entry.revalidate = consumers.length ? () => run(reloadResource) : null;
  const intervals = consumers.map((consumer) => consumer.intervalMs).filter((interval) => interval > 0);
  entry.timerId = intervals.length
    ? window.setInterval(() => run(loadResource), Math.min(...intervals))
    : null;
}

// Same-key subscribers share their snapshot, pending request and polling timer.
export function useSharedResource(key, fetcher, { intervalMs = 30000 } = {}) {
  const entry = getResourceEntry(key);
  const [, setTick] = useState(0);
  const fetcherRef = useRef({ key, fetcher });
  fetcherRef.current = { key, fetcher };
  const refresh = useCallback(() => reloadResource(key, () => fetcherRef.current.key === key ? fetcherRef.current.fetcher() : fetcher()), [key]);

  useEffect(() => {
    const listener = () => setTick((value) => value + 1);
    const current = getResourceEntry(key);
    const fetchCurrent = () => fetcherRef.current.key === key ? fetcherRef.current.fetcher() : fetcher();
    current.consumers ??= new Map();
    current.listeners.add(listener);
    current.consumers.set(listener, { fetcher: fetchCurrent, intervalMs });
    configurePolling(key, current);
    if (current.listeners.size === 1) loadResource(key, fetchCurrent).catch(() => undefined);
    return () => {
      current.listeners.delete(listener);
      current.consumers.delete(listener);
      configurePolling(key, current);
    };
  }, [key, intervalMs]);

  return { data: entry.data, error: entry.error, loading: entry.loading, refresh };
}

// 기업 목록이 정해지면 기업별 실시간 수집 요약을 병렬로 모아 하나의 공유 자원으로 캐싱한다.
export function useMonitoringSummaries(companies) {
  const ids = companies.map((company) => company.id).join(",");
  const { data, ...rest } = useSharedResource(
    `monitoring-summaries:${ids}`,
    async () => {
      const results = await Promise.allSettled(companies.map((company) => api.get(`/companies/${company.id}/monitoring`)));
      return Object.fromEntries(
        results.flatMap((result, index) => result.status === "fulfilled" ? [[companies[index].id, result.value.data]] : []),
      );
    },
    { intervalMs: ids ? 30000 : 0 },
  );
  return { ...rest, data: data ?? {} };
}
