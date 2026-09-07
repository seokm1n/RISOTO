const SELECTED_RISK_EVENT_KEY = "risoto:analysis-pipeline-risk-selection";

const positiveId = (value) => (/^\d+$/.test(String(value ?? "")) ? String(value) : "");

export function getAnalysisPipelineRiskEventId(companyId) {
  const normalizedCompanyId = positiveId(companyId);
  if (!normalizedCompanyId) return null;
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(SELECTED_RISK_EVENT_KEY) ?? "null");
    if (positiveId(stored?.companyId) !== normalizedCompanyId) return null;
    const riskEventId = positiveId(stored?.riskEventId);
    return riskEventId ? Number(riskEventId) : null;
  } catch {
    return null;
  }
}

export function setAnalysisPipelineRiskEventId(companyId, riskEventId) {
  const normalizedCompanyId = positiveId(companyId);
  const normalizedRiskEventId = positiveId(riskEventId);
  try {
    if (!normalizedCompanyId || !normalizedRiskEventId) {
      window.sessionStorage.removeItem(SELECTED_RISK_EVENT_KEY);
      return;
    }
    window.sessionStorage.setItem(SELECTED_RISK_EVENT_KEY, JSON.stringify({
      companyId: normalizedCompanyId,
      riskEventId: normalizedRiskEventId,
    }));
  } catch {
    // 저장소를 사용할 수 없는 환경에서도 URL 기반 선택은 계속 동작한다.
  }
}

export function clearAnalysisPipelineRiskEventId() {
  try {
    window.sessionStorage.removeItem(SELECTED_RISK_EVENT_KEY);
  } catch {
    // 세션 저장소 접근이 제한되어도 화면 이동은 계속 진행한다.
  }
}
