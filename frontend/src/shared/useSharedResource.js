import { useEffect, useRef, useState } from "react";

import { api } from "../api";

// 여러 화면이 같은 데이터(예: 기업 목록, 수집 헬스)를 각자 폴링하며 중복 요청하던 문제를
// 없애기 위한 공유 캐시다. 같은 key를 구독하는 컴포넌트가 몇 개든 폴링 타이머와 요청은
// 하나만 유지되고, 새 결과가 도착하면 모든 구독자가 함께 다시 렌더링된다.
const registry = new Map();

function getEntry(key) {
  let entry = registry.get(key);
  if (!entry) {
    entry = { data: undefined, error: null, loading: true, listeners: new Set(), timerId: null, inFlight: null };
    registry.set(key, entry);
  }
  return entry;
}

function notify(entry) {
  entry.listeners.forEach((listener) => listener());
}

function load(key, fetcher) {
  const entry = getEntry(key);
  if (entry.inFlight) return entry.inFlight;
  const request = fetcher()
    .then((data) => {
      entry.data = data; entry.error = null; entry.loading = false; entry.inFlight = null;
      notify(entry);
      return data;
    })
    .catch((error) => {
      entry.error = error; entry.loading = false; entry.inFlight = null;
      notify(entry);
      throw error;
    });
  entry.inFlight = request;
  return request;
}

// 사용자 동작 직후의 갱신은 기존 폴링 요청이 진행 중이어도 그 요청 뒤에 한 번 더 조회해
// 변경 전 응답이 네비게이션 같은 공용 UI에 남지 않게 한다.
function reload(key, fetcher) {
  const entry = getEntry(key);
  if (!entry.inFlight) return load(key, fetcher);
  return entry.inFlight.catch(() => undefined).then(() => load(key, fetcher));
}

// key가 같으면 어떤 화면에서 호출하든 같은 캐시·폴링을 공유한다.
// intervalMs가 0/false면 최초 1회만 불러오고 자동 재폴링하지 않는다(예: 산업군 같은 정적 참조 데이터).
export function useSharedResource(key, fetcher, { intervalMs = 30000 } = {}) {
  const entry = getEntry(key);
  const [, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const listener = () => setTick((value) => value + 1);
    const current = getEntry(key);
    current.listeners.add(listener);
    if (current.listeners.size === 1) {
      load(key, () => fetcherRef.current());
      if (intervalMs) current.timerId = window.setInterval(() => load(key, () => fetcherRef.current()), intervalMs);
    }
    return () => {
      current.listeners.delete(listener);
      if (current.listeners.size === 0 && current.timerId) {
        window.clearInterval(current.timerId);
        current.timerId = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, intervalMs]);

  return { data: entry.data, error: entry.error, loading: entry.loading, refresh: () => reload(key, () => fetcherRef.current()) };
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
