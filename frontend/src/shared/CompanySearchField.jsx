import { useEffect, useId, useState } from "react";

// 브리핑 홈과 분석 파이프라인 양쪽에서 같은 방식으로 기업을 검색·전환한다.
// Enter로 확정하지 않는다: 네이티브 datalist가 입력창 안에서 Enter를 자동완성
// 확정용으로 먼저 가로채는 경우가 있어(우리 onKeyDown까지 안 옴), 입력값이
// 기업명과 정확히 일치하는 순간 onChange에서 바로 전환한다.
export default function CompanySearchField({ companies, selectedCompany, onSelect, className = "brief-search" }) {
  const listId = useId();
  const [query, setQuery] = useState(selectedCompany?.name ?? "");
  useEffect(() => { setQuery(selectedCompany?.name ?? ""); }, [selectedCompany?.name]);

  const handleInput = (value) => {
    setQuery(value);
    const needle = value.trim().toLowerCase();
    const match = companies.find((company) => company.name.toLowerCase() === needle);
    if (match && match.id !== selectedCompany?.id) onSelect(match.id);
  };
  const revertIfInvalid = () => {
    if (!companies.some((company) => company.name === query)) setQuery(selectedCompany?.name ?? "");
  };

  return <label className={className}>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
    <input
      list={listId} value={query}
      onChange={(event) => handleInput(event.target.value)}
      onBlur={revertIfInvalid}
      onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }}
      placeholder="기업 검색" aria-label="기업 검색"
    />
    <datalist id={listId}>{companies.map((company) => <option value={company.name} key={company.id} />)}</datalist>
  </label>;
}
