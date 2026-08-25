import axios from "axios";

// 모든 화면이 공유하는 API 기준 주소, 제한 시간과 JSON 헤더를 중앙에서 설정한다.
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1",
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

export function getErrorMessage(error) {
  // 서버 응답 형식에 맞춰 사용자에게 표시할 오류 메시지를 추출한다.
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg).join(", ");
  return error.message || "요청을 처리하지 못했습니다.";
}
