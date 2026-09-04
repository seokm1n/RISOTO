import { api, getErrorMessage } from "../../api";
import { useSharedResource } from "../../shared/useSharedResource";
import { CompanyCard } from "./CompanyPages";

// 비교 기업 등록과 비교 기업별 수집·모니터링 현황을 한곳에 모은 화면이다.
// 메인 화면에서는 우리 기업 요약만 보여주고, 비교 기업 관리는 이 화면으로 분리했다.
export default function CompetitorsPage({ onOpenCompany, onRegister, onEditCompany }) {
  const { data: companies = [], error: companiesError, loading } = useSharedResource(
    "/companies", () => api.get("/companies").then((response) => response.data),
  );
  const competitors = companies.filter((company) => company.company_role === "competitor");
  const error = companiesError ? getErrorMessage(companiesError) : null;

  return <section className="workspace">
    <div className="workspace-head">
      <div><span className="eyebrow">COMPETITOR TRACKING / 03</span><h1>비교 기업</h1><p>등록한 비교 기업의 수집 상태와 모니터링 현황을 확인합니다.</p></div>
      <button className="primary-action" type="button" onClick={onRegister}><span>비교 기업 등록</span><b aria-hidden="true">＋</b></button>
    </div>
    {error && <div className="notice error">{error}</div>}
    {loading ? <p className="empty-state">비교 기업 정보를 불러오는 중입니다.</p> : competitors.length ? <div className="company-list">{competitors.map((company) => <CompanyCard company={company} key={company.id} onOpen={onOpenCompany} onEdit={onEditCompany} />)}</div> : <div className="empty-state home-empty"><p>아직 등록한 비교 기업이 없습니다.</p><button type="button" onClick={onRegister}>비교 기업 등록하기</button></div>}
  </section>;
}
