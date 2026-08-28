import { api, getErrorMessage } from "../../api";
import { useMonitoringSummaries, useSharedResource } from "../../shared/useSharedResource";
import { CompanyCard } from "./CompanyPages";

// 경쟁사 등록과 경쟁사별 수집·모니터링 현황을 한곳에 모은 화면이다.
// 메인 화면에서는 우리 기업 요약만 보여주고, 경쟁사 비교·관리는 이 화면으로 분리했다.
export default function CompetitorsPage({ onOpenCompany, onRegister, onEditCompany }) {
  const { data: companies = [], error: companiesError, loading } = useSharedResource(
    "/companies", () => api.get("/companies").then((response) => response.data),
  );
  const competitors = companies.filter((company) => company.company_role === "competitor");
  const { data: monitoringSummaries } = useMonitoringSummaries(competitors);
  const error = companiesError ? getErrorMessage(companiesError) : null;

  return <section className="workspace">
    <div className="workspace-head">
      <div><span className="eyebrow">COMPETITOR TRACKING / 03</span><h1>경쟁사</h1><p>등록한 경쟁사의 수집 상태와 모니터링 현황을 확인합니다.</p></div>
      <button className="primary-action" type="button" onClick={onRegister}><span>경쟁사 등록</span><b aria-hidden="true">＋</b></button>
    </div>
    {error && <div className="notice error">{error}</div>}
    {loading ? <p className="empty-state">경쟁사 정보를 불러오는 중입니다.</p> : competitors.length ? <div className="company-list">{competitors.map((company) => <CompanyCard company={company} key={company.id} monitoringSummary={monitoringSummaries[company.id]} onOpen={onOpenCompany} onEdit={onEditCompany} />)}</div> : <div className="empty-state home-empty"><p>아직 등록한 경쟁사가 없습니다.</p><button type="button" onClick={onRegister}>경쟁사 등록하기</button></div>}
  </section>;
}
