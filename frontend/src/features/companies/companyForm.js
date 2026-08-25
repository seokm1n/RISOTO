import { COMPANY_KEYWORD_FIELDS } from "../../shared/presentation";

// API 기업 응답을 수정 폼에서 직접 다룰 수 있는 유형별 배열 구조로 변환한다.
export const companyToForm = (company) => {
  const keywords = { alias: [], product: [], risk: [] };
  company?.keywords?.forEach((keyword) => keywords[keyword.keyword_type]?.push(keyword.value));
  return {
    name: company?.name ?? "",
    ticker: company?.ticker ?? "",
    industryId: company?.industry_id ? String(company.industry_id) : "",
    annualRevenue: company?.annual_revenue_100m_krw ?? "",
    sizeClass: company?.company_size_class ?? "",
    aliases: keywords.alias,
    products: keywords.product,
    risks: keywords.risk,
  };
};

// 키워드 표시 순서와 무관하게 수정 여부를 비교할 안정적인 폼 서명을 만든다.
export const companyFormSignature = (form) => JSON.stringify({
  name: form.name.trim().replace(/\s+/g, " "),
  ticker: form.ticker.trim().toUpperCase(),
  industryId: form.industryId,
  annualRevenue: form.annualRevenue.trim(),
  sizeClass: form.sizeClass,
  aliases: [...form.aliases].sort(),
  products: [...form.products].sort(),
  risks: [...form.risks].sort(),
});

// 아직 칩으로 확정하지 않은 입력도 중복 없이 최종 키워드 목록에 포함한다.
export const mergeKeywordDraft = (values, draft) => {
  const normalized = draft.trim().replace(/\s+/g, " ");
  if (!normalized || values.some((value) => value.toLocaleLowerCase() === normalized.toLocaleLowerCase())) return values;
  return [...values, normalized];
};

// 네 종류의 키워드 초안을 각 폼 목록에 병합해 제출 직전 상태를 만든다.
export const companyKeywordsWithDrafts = (form, drafts) => Object.fromEntries(
  COMPANY_KEYWORD_FIELDS.map(({ field }) => [field, mergeKeywordDraft(form[field], drafts[field])]),
);

// 공통 기업 폼에서 사용하는 빈 키워드 초안 상태를 새 객체로 만든다.
export const emptyKeywordDrafts = () => ({ aliases: "", products: "", risks: "" });

export const isValidAnnualRevenue = (value) => {
  const normalized = value.trim();
  return /^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(normalized) && Number(normalized) > 0;
};

// 유형별 키워드 배열을 기업 API가 받는 평면 요청 목록으로 변환한다.
export const companyKeywordPayload = (keywords) => COMPANY_KEYWORD_FIELDS.flatMap(({ field, type }) => (
  keywords[field].map((value) => ({ keyword_type: type, value }))
));
