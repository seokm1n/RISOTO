const SELECTED_COMPANY_KEY = "risoto:selected-company-id";

export function getSelectedCompanyId() {
  try {
    const value = window.sessionStorage.getItem(SELECTED_COMPANY_KEY);
    return /^\d+$/.test(value ?? "") ? value : "";
  } catch {
    return "";
  }
}

export function setSelectedCompanyId(companyId) {
  try {
    if (companyId) window.sessionStorage.setItem(SELECTED_COMPANY_KEY, String(companyId));
    else window.sessionStorage.removeItem(SELECTED_COMPANY_KEY);
  } catch {
    // 저장소를 사용할 수 없는 환경에서도 URL 기반 선택은 계속 동작한다.
  }
}

export function clearSelectedCompanyId() {
  setSelectedCompanyId(null);
}

export function resolveSelectedCompany(companies, requestedCompanyId = "") {
  const requested = companies.find((company) => String(company.id) === String(requestedCompanyId));
  if (requested) return requested;
  const storedCompanyId = getSelectedCompanyId();
  return companies.find((company) => String(company.id) === storedCompanyId)
    ?? companies.find((company) => company.company_role === "main")
    ?? companies[0]
    ?? null;
}
