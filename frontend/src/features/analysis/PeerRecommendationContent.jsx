// 동종 기업 추천(schema_version 3, generation_kind: competitor_impact)을 그린다.
//
// 메인 경로와도 v2와도 구조가 다르다. scenarios가 아예 없고 content.impact(영향 판단)와
// content.recommendation(권고)을 읽는다. 그래서 MainResponseContent와 같은 이유로 파일을
// 나눴다.
//
// 그리는 상태가 셋이다.
//   근거부족_보류  기사가 0건이라 판단 자체를 하지 않음 (LLM 미호출). 양쪽 경로 공통이라
//                  MainResponseContent의 NoEvidenceNotice를 그대로 쓴다 - 같은 상태를 두
//                  화면으로 그리면 담당자가 다른 상황으로 읽는다.
//   영향없음_종료  영향 판단이 '영향_없음'을 내서 추천을 만들지 않음. recommendation이 null.
//   생성완료/검증실패  추천이 있음.
//
// 두 번째가 이 경로의 핵심이다. "만들다 실패했다"가 아니라 "만들지 않기로 했다"는 것이
// 화면에서 읽혀야 게이트가 기능으로 보인다.
import { NoEvidenceNotice } from "./MainResponseContent";

// 내부 표기(언더스코어)를 사람 말로 바꾼다. 검증 규칙 6이 산문 필드에서 이 표기를
// 금지하는데, 구조 필드는 그대로 두고 화면에서 바꾸는 것이 원래 방침이다.
const DIRECTION_LABELS = {
  부정적_파급: "부정적 파급",
  반사이익: "반사이익",
  영향_없음: "영향 없음",
};

const CHANNEL_LABELS = {
  규제_조사_확대: "규제·조사 확대",
  소비자_인식_전이: "소비자 인식 전이",
  투자자_주가_동조: "투자자·주가 동조",
  동일_취약점_보유: "동일 취약점 보유",
  공급망_협력사_공유: "공급망·협력사 공유",
  고객_유입_기회: "고객 유입 기회",
};

const TIMEFRAME_LABELS = {
  즉시: "즉시",
  "1주_내": "1주 이내",
  "2주_내": "2주 이내",
  "1개월_내": "1개월 이내",
};

// 시점 정렬 기준. 목록 위쪽이 '먼저 할 일'로 읽히는데 실제로는 모델이 낸 순서일 뿐이라
// 화면에서 다시 세운다. 모르는 값은 맨 뒤로 보내되 버리지는 않는다.
const TIMEFRAME_ORDER = ["즉시", "1주_내", "2주_내", "1개월_내"];

function timeframeRank(timeframe) {
  const index = TIMEFRAME_ORDER.indexOf(timeframe);
  return index === -1 ? TIMEFRAME_ORDER.length : index;
}

function label(map, value) {
  return map[value] ?? value ?? "";
}

function percent(value) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : null;
}

// 영향 판단 요약. 추천이 있든 없든 머리 부분에 온다.
function ImpactVerdict({ impact, peerCompanyName }) {
  const confidence = percent(impact.confidence);
  const level = impact.impact_level;
  return (
    <section className="draft-impact">
      <h5>영향 판단</h5>
      <p className="draft-verdict">
        {label(DIRECTION_LABELS, impact.impact_direction)}
        {level && level !== "없음" ? ` · 노출 ${level}` : ""}
        {confidence ? ` · 확신도 ${confidence}` : ""}
        {impact.needs_review && <span className="draft-review-flag">사람 확인 필요</span>}
      </p>
      {impact.reason && <p className="draft-reason">{impact.reason}</p>}
      {impact.watch_points?.length > 0 && (
        <>
          <strong>지켜볼 것</strong>
          <ul>
            {impact.watch_points.map((point, i) => (
              <li key={`wp-${i}`}>{point}</li>
            ))}
          </ul>
        </>
      )}
      {peerCompanyName && (
        <p className="draft-peer-note">
          {peerCompanyName}에서 발생한 사안이며, 우리 기업의 자체 사고가 아닙니다.
        </p>
      )}
    </section>
  );
}

// 권고 하나. verify_first는 사실 확인 전에 대외 발언하지 말라는 뜻이라 놓치면 사고로
// 이어진다. 목록에서 눈에 띄어야 한다.
function RecommendationItem({ item, index, orphanChannel }) {
  return (
    <li className="draft-recommendation">
      <div className="draft-recommendation-head">
        <span className="draft-order">{String(index).padStart(2, "0")}</span>
        <span className="draft-timeframe">{label(TIMEFRAME_LABELS, item.timeframe)}</span>
        {item.verify_first && <em className="draft-verify-first">확인 후 대외 답변</em>}
      </div>
      <p className="draft-action">{item.action}</p>
      {orphanChannel && (
        <p className="draft-orphan-channel">
          경로: {label(CHANNEL_LABELS, item.channel)} · 앞 단계가 식별한 영향 경로에 없음
        </p>
      )}
      {item.owner_hint && <p className="draft-owner">담당: {item.owner_hint}</p>}
      {item.rationale && <p className="draft-rationale">{item.rationale}</p>}
    </li>
  );
}

// 권고를 영향 경로로 묶는다. 검증 규칙 1이 "모든 권고는 영향 경로 중 하나에 매달려야
// 한다"고 강제하는데, 평평한 목록으로 그리면 그 구조가 화면에서 사라진다.
function RecommendationGroups({ recommendations, channels }) {
  const known = channels ?? [];
  const orphans = recommendations.filter((item) => !known.includes(item.channel));
  let order = 0;

  const groups = known.map((channel) => ({
    channel,
    label: label(CHANNEL_LABELS, channel),
    items: recommendations
      .filter((item) => item.channel === channel)
      .sort((a, b) => timeframeRank(a.timeframe) - timeframeRank(b.timeframe)),
    orphan: false,
  }));

  // 경로 밖 권고는 버리지 않고 맨 아래에 모은다. 검증 실패 건도 사람이 보고 판단해야
  // 하므로 화면에서 사라지면 안 된다.
  if (orphans.length > 0) {
    groups.push({ channel: "__orphan__", label: "경로 불명", items: orphans, orphan: true });
  }

  return (
    <section className="draft-recommendations">
      <h5>권고 · 영향 경로별</h5>
      {groups.map((group) => (
        <div className="draft-channel-group" key={group.channel}>
          <strong className="draft-channel">{group.label}</strong>
          {group.items.length === 0 ? (
            // 앞 단계가 식별한 경로인데 권고가 없는 것은 그 자체로 봐야 할 신호다.
            <p className="draft-channel-empty">이 경로에 대한 권고가 없습니다.</p>
          ) : (
            <ul>
              {group.items.map((item, i) => {
                order += 1;
                return (
                  <RecommendationItem
                    key={`${group.channel}-${i}`}
                    item={item}
                    index={order}
                    orphanChannel={group.orphan}
                  />
                );
              })}
            </ul>
          )}
        </div>
      ))}
    </section>
  );
}

// 검증에서 걸린 항목. 통과했으면 알릴 것이 없다 - MainResponseContent와 같은 방침이다.
function VerificationBadge({ verification }) {
  const violations = verification?.violations ?? [];
  if (!verification || (verification.passed && violations.length === 0)) return null;
  return (
    <div className="draft-verification">
      <strong>검증에서 걸린 항목</strong>
      <ul>
        {violations.map((v, i) => (
          <li key={`v-${i}`}>{typeof v === "string" ? v : v.message ?? JSON.stringify(v)}</li>
        ))}
      </ul>
    </div>
  );
}

export default function PeerRecommendationContent({ content }) {
  // 근거 기사가 없어 판단 자체를 하지 않은 경우. 이 content에는 impact도 content_kind도
  // 없으므로 다른 것을 읽기 전에 먼저 가른다.
  if (content.status === "근거부족_보류") {
    return <NoEvidenceNotice content={content} />;
  }

  const impact = content.impact ?? {};
  const recommendation = content.recommendation;
  const recommendations = recommendation?.recommendations ?? [];
  const citedIds = recommendation?.cited_case_ids ?? [];
  const cases = content.cases ?? [];
  const citedCases = cases.filter((c) => citedIds.includes(c.case_id));

  return (
    <div className="response-draft response-draft-v3">
      <div className="response-draft-head">
        <div>
          <span className="eyebrow">동종 기업 추천 · 검토 필요</span>
          <strong>
            {content.peer_company_name ? `${content.peer_company_name}에서 발생한 사안` : "동종 기업 사안"}
            {content.risk_type_label ? ` · ${content.risk_type_label}` : ""}
          </strong>
        </div>
        <span className="draft-kind competitor">비교 기업 → 나의 기업 영향</span>
      </div>

      <ImpactVerdict impact={impact} peerCompanyName={content.peer_company_name} />

      {/* 영향_없음이면 추천이 없다. "만들다 실패했다"로 읽히지 않도록 이유를 명시한다. */}
      {content.status === "영향없음_종료" && (
        <section className="draft-no-recommendation">
          <h5>권고를 만들지 않았습니다</h5>
          <p>
            우리 기업에 닿는 영향 경로가 확인되지 않아 여기서 종료했습니다. 상황이 바뀌면
            다시 판단합니다.
          </p>
        </section>
      )}

      {recommendation && (
        <>
          {recommendation.source_event && (
            <section className="draft-situation">
              <h5>상황</h5>
              <p>{recommendation.source_event}</p>
            </section>
          )}

          {recommendation.headline && (
            <section className="draft-headline">
              <h5>무엇을 할 것인가</h5>
              <p>{recommendation.headline}</p>
            </section>
          )}

          {recommendations.length > 0 && (
            <RecommendationGroups
              recommendations={recommendations}
              channels={impact.impact_channels}
            />
          )}

          {recommendation.avoid?.length > 0 && (
            <section className="draft-avoid">
              <h5>하지 말 것</h5>
              <ul>
                {recommendation.avoid.map((item, i) => (
                  <li key={`av-${i}`}>{item}</li>
                ))}
              </ul>
            </section>
          )}

          {recommendation.realert_condition && (
            <section className="draft-realert">
              <h5>다시 볼 조건</h5>
              <p>{recommendation.realert_condition}</p>
            </section>
          )}

          {recommendation.limitations && (
            <p className="draft-limitations">
              <strong>한계</strong>
              {recommendation.limitations}
            </p>
          )}
        </>
      )}

      {content.regulations?.length > 0 && (
        <section className="draft-regulations">
          <h5>참고 법령</h5>
          <ul>
            {content.regulations.map((r, i) => (
              <li key={`rg-${i}`}>
                <strong>
                  {r.law_name} {r.article}
                </strong>
                <span>{r.requirement}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {citedCases.length > 0 && (
        <section className="draft-precedents">
          <h5>인용 사례</h5>
          <ul>
            {citedCases.map((c, i) => {
              const url = (c.source_urls ?? [])[0];
              return (
                <li key={`pc-${i}`}>
                  {url ? (
                    <a href={url} target="_blank" rel="noreferrer">
                      {c.title}
                    </a>
                  ) : (
                    <span>{c.title}</span>
                  )}
                  {/* 지금 case_records가 0행이라 인용 사례는 사실상 전부 웹검색이다.
                      표시가 없으면 검수된 것처럼 읽힌다. */}
                  {c.provenance === "web_search" && <em className="unverified">미검수 · 참고용</em>}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <VerificationBadge verification={content.verification} />
    </div>
  );
}
