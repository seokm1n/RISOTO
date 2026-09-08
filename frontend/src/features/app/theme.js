import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "risoto.theme";
export const THEMES = ["warm", "dark"];

// 첫 페인트 전에 적용해야 화면이 한 번 밝게 번쩍이지 않는다. index.html의
// 인라인 스크립트가 이미 <html data-theme>을 채워두므로 여기서는 그 값을 읽는다.
export function readStoredTheme() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (THEMES.includes(saved)) return saved;
  } catch {
    // 사생활 보호 모드 등에서 저장소 접근이 막힐 수 있다. 기본값으로 간다.
  }
  const attribute = document.documentElement.dataset.theme;
  return THEMES.includes(attribute) ? attribute : "warm";
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // 저장에 실패해도 이번 세션 동안은 정상 동작한다.
  }
}

export function useTheme() {
  const [theme, setTheme] = useState(readStoredTheme);

  useEffect(() => { applyTheme(theme); }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === "dark" ? "warm" : "dark"));
  }, []);

  return { theme, setTheme, toggleTheme };
}
