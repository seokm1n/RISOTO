import { useSearchParams } from "react-router";

import CollectionPage from "../collection/CollectionPage";
import CompanyAdministrationPage from "../companies/CompanyPages";

const SECTIONS = [
  { id: "collection", label: "수집 현황", description: "실시간 수집 상태와 기업별 수집 이력" },
  { id: "companies", label: "기업 관리", description: "나의 기업과 비교 기업 정보" },
];

// 수집 현황과 기업 관리는 둘 다 "설정을 손보는" 화면이라 상단 메뉴를 각각 차지할
// 이유가 없다. 하나의 관리 탭 아래 두고 안에서 전환한다. 각 화면은 자기 workspace를
// 그대로 쓰므로 여기서는 전환 컨트롤만 얹는다.
export default function ManagementPage({ collectionProps = {}, companyProps = {} }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("section");
  const section = SECTIONS.some((item) => item.id === requested) ? requested : "collection";
  const current = SECTIONS.find((item) => item.id === section);

  const selectSection = (nextSection) => {
    setSearchParams((params) => {
      const next = new URLSearchParams(params);
      next.set("section", nextSection);
      return next;
    });
  };

  return <div className="management-page">
    <div className="management-page-head">
      <div>
        <p className="eyebrow">WORKSPACE SETTINGS</p>
        <h1>관리</h1>
        <p className="management-page-description">{current.description}</p>
      </div>
      <div className="management-section-tabs" role="tablist" aria-label="관리 화면 선택">
        {SECTIONS.map((item) => <button
          type="button"
          role="tab"
          key={item.id}
          className={section === item.id ? "active" : ""}
          aria-selected={section === item.id}
          onClick={() => selectSection(item.id)}
        >{item.label}</button>)}
      </div>
    </div>
    {section === "collection"
      ? <CollectionPage {...collectionProps} />
      : <CompanyAdministrationPage {...companyProps} />}
  </div>;
}
