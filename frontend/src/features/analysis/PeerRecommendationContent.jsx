// 두 컴포넌트를 메인 렌더러에서 가져다 쓴다. 같은 것을 두 번 만들면 한쪽만 고쳐졌을 때
// 담당자가 두 화면을 다른 상황으로 읽는다 - NoEvidenceNotice를 공유하는 이유와 같다.
import { BulletText, FoldSection, NoEvidenceNotice } from "./MainResponseContent";

const DIRECTION_PRESENTATION = {
  부정적_파급: { label: "부정 영향 가능", tone: "urgent" },
  반사이익: { label: "기회 영향 가능", tone: "opportunity" },
  영향_없음: { label: "직접 영향 낮음", tone: "watch" },
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

const TIMEFRAME_ORDER = ["즉시", "1주_내", "2주_내", "1개월_내"];

function humanize(value) {
  return typeof value === "string" ? value.replaceAll("_", " ") : value ?? "";
}

function label(map, value) {
  return map[value] ?? humanize(value);
}

function percent(value) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : null;
}

function timeframeRank(value) {
  const rank = TIMEFRAME_ORDER.indexOf(value);
  return rank === -1 ? TIMEFRAME_ORDER.length : rank;
}

function ImpactHeader({ content, impact, recommendationCount }) {
  const direction = DIRECTION_PRESENTATION[impact.impact_direction] ?? {
    label: label({}, impact.impact_direction) || "영향 방향 확인",
    tone: "standard",
  };
  const facts = [
    ["위험 유형", content.risk_type_label],
    ["영향 수준", impact.impact_level && impact.impact_level !== "없음" ? impact.impact_level : "낮음"],
    ["판단 확신도", percent(impact.confidence)],
    ["권고 과제", recommendationCount ? `${recommendationCount}개` : null],
  ].filter(([, value]) => value);

  return (
    <section className={`response-command-card response-peer-command ${direction.tone}`}>
      <div className="response-command-copy">
        <span className="response-ui-kicker">동종 업계 영향 가이드</span>
        <div className="response-command-title">
          <span className={`response-priority-pill ${direction.tone}`}>{direction.label}</span>
          <h4>
            {content.peer_company_name
              ? `${content.peer_company_name} 관련 사안`
              : "동종 기업 관련 사안"}
          </h4>
        </div>
        {impact.reason && <p>{impact.reason}</p>}
        {content.peer_company_name && (
          <small className="response-peer-context">
            우리 기업에서 발생한 사건이 아니라 업계 파급 가능성을 분석한 결과입니다.
          </small>
        )}
      </div>
      {facts.length > 0 && (
        <dl className="response-command-facts">
          {facts.map(([name, value]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

function RecommendationGroups({ recommendations, channels }) {
  const knownChannels = channels ?? [];
  const orphaned = recommendations.filter((item) => !knownChannels.includes(item.channel));
  const groups = knownChannels.map((channel) => ({
    channel,
    title: label(CHANNEL_LABELS, channel),
    items: recommendations
      .filter((item) => item.channel === channel)
      .sort((left, right) => timeframeRank(left.timeframe) - timeframeRank(right.timeframe)),
    orphaned: false,
  }));

  if (orphaned.length > 0) {
    groups.push({
      channel: "unclassified",
      title: "기타 영향 경로",
      items: [...orphaned].sort(
        (left, right) => timeframeRank(left.timeframe) - timeframeRank(right.timeframe)
      ),
      orphaned: true,
    });
  }

  let order = 0;
  return (
    <section className="response-workboard response-peer-workboard">
      <header className="response-section-heading">
        <div>
          <span>실행 권고</span>
          <h4>우리 기업이 준비할 일</h4>
        </div>
        <strong>{recommendations.length}개 과제</strong>
      </header>
      <div className="response-channel-groups">
        {groups.map((group) => (
          <article className="response-channel-group" key={group.channel}>
            <header>
              <strong>{group.title}</strong>
              <span>{group.items.length}개</span>
            </header>
            {group.items.length > 0 ? (
              <ol>
                {group.items.map((item, index) => {
                  order += 1;
                  return (
                    <li key={`${group.channel}-${index}`}>
                      <span className="response-task-number">
                        {String(order).padStart(2, "0")}
                      </span>
                      <div className="response-task-copy">
                        <div className="response-task-flags">
                          {item.verify_first && <em>사실 확인 먼저</em>}
                          {group.orphaned && <em className="muted">경로 재확인</em>}
                        </div>
                        <strong>{item.action}</strong>
                        {item.owner_hint && <small>담당 · {item.owner_hint}</small>}
                        {item.rationale && <p>{item.rationale}</p>}
                      </div>
                      <span className="response-task-due">
                        {label(TIMEFRAME_LABELS, item.timeframe) || "시점 확인"}
                      </span>
                    </li>
                  );
                })}
              </ol>
            ) : (
              <p className="response-channel-empty">이 경로에 연결된 권고가 없습니다.</p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function FollowUpSection({ impact, recommendation }) {
  const watchPoints = impact.watch_points ?? [];
  const avoid = recommendation?.avoid ?? [];
  if (
    !watchPoints.length &&
    !avoid.length &&
    !recommendation?.realert_condition &&
    !recommendation?.limitations
  ) {
    return null;
  }

  return (
    <section className="response-followup-panel">
      <header className="response-section-heading">
        <div>
          <span>후속 관리</span>
          <h4>하지 말아야 할 일과 후속 점검</h4>
        </div>
      </header>
      {/* 네 칸 중 이것만 펼쳐 둔다. 위 실행 권고에 없는 유일한 정보이고, 반사이익
          사안에서는 검증 규칙 4가 비어 있으면 위반으로 잡는 필수 항목이라 접으면
          안 된다. 색이 들어간 카드도 여기 하나뿐이어야 시선이 갈린다. */}
      {avoid.length > 0 && (
        <article className="response-avoid-card">
          <h5>하지 말아야 할 일</h5>
          <ul>
            {avoid.map((item, index) => (
              <li key={`avoid-${index}`}>{item}</li>
            ))}
          </ul>
        </article>
      )}
      <div className="response-fold-stack">
        {/* 지켜볼 신호는 위 실행 권고와 내용이 겹친다 - impact가 낸 영향 경로를
            recommend가 다시 받아 권고를 만드니 같은 축을 두 번 말하게 된다.
            (실측: 조사 확대 여부/계정·결제정보 유출/외부 로그인·휴면 계정/고객 문의가
            각각 권고 01·04·04·02·03과 대응) 그래서 기본은 접어 두고, 몇 건인지는
            펼치지 않아도 보이게 요약에 적는다. */}
        {watchPoints.length > 0 && (
          <FoldSection title={`지켜볼 신호 ${watchPoints.length}건`}>
            <ul>
              {watchPoints.map((point, index) => (
                <li key={`watch-${index}`}>{point}</li>
              ))}
            </ul>
          </FoldSection>
        )}
        {recommendation?.realert_condition && (
          <FoldSection title="다시 알릴 기준">
            <BulletText text={recommendation.realert_condition} />
          </FoldSection>
        )}
        {recommendation?.limitations && (
          <FoldSection title="사용 전 확인">
            <BulletText text={recommendation.limitations} />
          </FoldSection>
        )}
      </div>
    </section>
  );
}

function VerificationNotice({ verification }) {
  const violations = verification?.violations ?? [];
  if (!verification || (verification.passed && violations.length === 0)) return null;
  return (
    <aside className="response-quality-alert" role="status">
      <strong>자동 검증에서 확인이 필요한 항목</strong>
      <ul>
        {violations.map((violation, index) => (
          <li key={`peer-verification-${index}`}>
            {typeof violation === "string"
              ? violation
              : violation.message ?? "세부 검증 결과를 확인해 주세요."}
          </li>
        ))}
      </ul>
    </aside>
  );
}

export default function PeerRecommendationContent({ content }) {
  if (content.status === "근거부족_보류") {
    return <NoEvidenceNotice content={content} />;
  }

  const impact = content.impact ?? {};
  const recommendation = content.recommendation;
  const recommendations = recommendation?.recommendations ?? [];

  return (
    <div className="response-draft response-draft-v3 response-operations-view response-peer-view">
      <ImpactHeader
        content={content}
        impact={impact}
        recommendationCount={recommendations.length}
      />

      {recommendation && (
        <section className="response-peer-brief">
          {recommendation.source_event && (
            <article>
              <h5>동종 기업에서 일어난 일</h5>
              <BulletText text={recommendation.source_event} />
            </article>
          )}
          {recommendation.headline && (
            <article className="recommended">
              <h5>현재 권고</h5>
              <p>{recommendation.headline}</p>
            </article>
          )}
        </section>
      )}

      {content.status === "영향없음_종료" && (
        <section className="response-no-action-card">
          <strong>현재 필요한 별도 대응은 없습니다</strong>
          <p>
            우리 기업으로 이어지는 영향 경로가 확인되지 않았습니다. 관련 신호가 달라지면
            다시 판단합니다.
          </p>
        </section>
      )}

      {recommendations.length > 0 && (
        <RecommendationGroups
          recommendations={recommendations}
          channels={impact.impact_channels}
        />
      )}

      <FollowUpSection impact={impact} recommendation={recommendation} />
      <VerificationNotice verification={content.verification} />
    </div>
  );
}
