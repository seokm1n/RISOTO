import { useState } from "react";

const STANCE_LABELS = {
  선제_공개: "선제 공개",
  사실확인_우선: "사실 확인 우선",
  피해구제_중심: "피해 구제 중심",
};

const TIER_PRESENTATION = {
  T1_관찰: {
    label: "관찰 대응",
    tone: "watch",
    description: "상황 변화를 관찰하면서 필요한 준비 항목을 점검합니다.",
  },
  T2_주시: {
    label: "주의 대응",
    tone: "caution",
    description: "담당 부서가 사실관계를 확인하고 대응 준비를 시작합니다.",
  },
  T3_긴급: {
    label: "긴급 대응",
    tone: "urgent",
    description: "즉시 담당 부서를 소집하고 우선 실행 항목부터 착수합니다.",
  },
};

const STRATEGY_LABELS = {
  사실관계_정정: "사실관계 정정",
  사과_시정: "사과·시정",
  보상: "보상",
  재발방지: "재발 방지",
  소통강화: "소통 강화",
  법적대응: "법적 대응",
  모니터링_유지: "모니터링 유지",
  부인_반박: "부인·반박",
};

const RESPONSIBILITY_LABELS = {
  피해자: "피해 구제 중심",
  사고: "사고 수습 중심",
  예방가능: "예방 가능성 있음",
};

const SEVERITY_LABELS = {
  critical: "긴급",
  warning: "주의",
  normal: "일반",
};

const TIME_BANDS = [
  { label: "즉시", limit: 6 },
  { label: "24시간 이내", limit: 24 },
  { label: "72시간 이내", limit: 72 },
  { label: "후속 조치", limit: Infinity },
];

function humanize(value) {
  return typeof value === "string" ? value.replaceAll("_", " ") : value ?? "";
}

function tierOf(value) {
  if (TIER_PRESENTATION[value]) return TIER_PRESENTATION[value];
  const fallback = humanize(value).replace(/^T\d+\s*/, "").trim();
  return {
    label: fallback ? `${fallback} 대응` : "대응 단계 확인",
    tone: "standard",
    description: "사건 상황에 맞춰 우선 실행 항목을 확인합니다.",
  };
}

function deadlineLabel(value) {
  const hours = Number(value);
  if (!Number.isFinite(hours)) return "기한 확인";
  if (hours <= 1) return "1시간 이내";
  return `${hours}시간 이내`;
}

function bandsOf(checklist) {
  const buckets = TIME_BANDS.map((band) => ({ ...band, items: [] }));
  const ordered = [...(checklist ?? [])].sort((left, right) => {
    const leftHours = Number.isFinite(Number(left.deadline_hours))
      ? Number(left.deadline_hours)
      : Infinity;
    const rightHours = Number.isFinite(Number(right.deadline_hours))
      ? Number(right.deadline_hours)
      : Infinity;
    return leftHours - rightHours;
  });

  for (const item of ordered) {
    const hours = Number.isFinite(Number(item.deadline_hours))
      ? Number(item.deadline_hours)
      : Infinity;
    const bucket = buckets.find((band) => hours <= band.limit) ?? buckets[buckets.length - 1];
    bucket.items.push(item);
  }
  return buckets.filter((band) => band.items.length > 0);
}

function ResponseHeader({ content, report }) {
  const tier = tierOf(content.tier);
  const policy = content.tier_policy ?? {};
  const facts = [
    ["우선 소통 대상", content.stakeholder],
    ["권장 검토", policy["검토"]],
    ["실행 과제", report?.checklist?.length ? `${report.checklist.length}개` : null],
  ].filter(([, value]) => value);

  return (
    <section className={`response-command-card ${tier.tone}`}>
      <div className="response-command-copy">
        <span className="response-ui-kicker">AI 대응 가이드</span>
        <div className="response-command-title">
          <span className={`response-priority-pill ${tier.tone}`}>{tier.label}</span>
          <h4>{content.risk_type_label ?? "위험 유형 확인 필요"}</h4>
        </div>
        <p>{tier.description}</p>
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

function ScenarioSelector({ scenarios, active, selected, onChange }) {
  if (scenarios.length <= 1) return null;
  return (
    <section className="response-option-panel" aria-label="대응안 선택">
      <header className="response-section-heading compact">
        <div>
          <span>대응 방향 선택</span>
          <h4>상황에 맞는 대응안을 확인하세요</h4>
        </div>
        <strong>{scenarios.length}개 안</strong>
      </header>
      <div className="response-option-tabs" role="tablist">
        {scenarios.map((scenario, index) => {
          const headline =
            scenario.report?.scenario_headline ||
            STANCE_LABELS[scenario.stance] ||
            humanize(scenario.stance) ||
            `${index + 1}번째 대응안`;
          return (
            <button
              type="button"
              role="tab"
              key={`${scenario.stance ?? "scenario"}-${index}`}
              className={`response-option-tab${index === active ? " active" : ""}`}
              aria-selected={index === active}
              onClick={() => onChange(index)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{headline}</strong>
              <small>{STANCE_LABELS[scenario.stance] ?? humanize(scenario.stance)}</small>
              {scenario.stance === selected && <em>기본안</em>}
            </button>
          );
        })}
      </div>
    </section>
  );
}

function SituationSection({ scenario }) {
  const report = scenario?.report ?? {};
  const points = report.summary_points ?? [];
  const assessment = report.risk_assessment ?? {};
  const primaryRisks = assessment.primary_risks ?? [];
  const secondaryRisks = assessment.secondary_risks ?? [];
  const tradeoff = scenario?.tradeoff || report.scenario_tradeoff;

  if (
    !points.length &&
    !primaryRisks.length &&
    !secondaryRisks.length &&
    !tradeoff
  ) {
    return null;
  }

  return (
    <section className="response-overview-panel">
      <header className="response-section-heading">
        <div>
          <span>상황 요약</span>
          <h4>{report.scenario_headline || "선택한 대응안"}</h4>
        </div>
        {assessment.responsibility && (
          <strong className="response-responsibility">
            {RESPONSIBILITY_LABELS[assessment.responsibility] ?? humanize(assessment.responsibility)}
          </strong>
        )}
      </header>

      {tradeoff && <p className="response-plan-note">{tradeoff}</p>}

      <div className="response-overview-grid">
        {points.length > 0 && (
          <article className="response-summary-card">
            <h5>핵심 상황</h5>
            <p className="response-summary-lead">{points[0]}</p>
            {points.length > 1 && (
              <ul>
                {points.slice(1).map((point, index) => (
                  <li key={`summary-${index}`}>{point}</li>
                ))}
              </ul>
            )}
          </article>
        )}

        {(primaryRisks.length > 0 || secondaryRisks.length > 0) && (
          <article className="response-risk-card">
            <h5>우선 확인할 위험</h5>
            {primaryRisks.length > 0 && (
              <ul className="primary">
                {primaryRisks.map((risk, index) => (
                  <li key={`primary-risk-${index}`}>{risk}</li>
                ))}
              </ul>
            )}
            {secondaryRisks.length > 0 && (
              <details className="response-minor-disclosure">
                <summary>추가 위험 {secondaryRisks.length}건</summary>
                <ul>
                  {secondaryRisks.map((risk, index) => (
                    <li key={`secondary-risk-${index}`}>{risk}</li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        )}
      </div>

    </section>
  );
}

function PlanSection({ report }) {
  const bands = bandsOf(report?.checklist);
  const taskCount = bands.reduce((total, band) => total + band.items.length, 0);
  if (!bands.length) return null;

  let order = 0;
  return (
    <section className="response-workboard">
      <header className="response-section-heading">
        <div>
          <span>실행 계획</span>
          <h4>지금부터 해야 할 일</h4>
        </div>
        <strong>{taskCount}개 과제</strong>
      </header>
      <div className="response-time-groups">
        {bands.map((band) => (
          <article className="response-time-group" key={band.label}>
            <header>
              <strong>{band.label}</strong>
              <span>{band.items.length}개</span>
            </header>
            <ol>
              {band.items.map((item, index) => {
                order += 1;
                return (
                  <li key={`${band.label}-${index}`}>
                    <span className="response-task-number">{String(order).padStart(2, "0")}</span>
                    <div className="response-task-copy">
                      <strong>{item.task}</strong>
                      <small>{item.owner ? `담당 · ${item.owner}` : "담당 부서 확인 필요"}</small>
                    </div>
                    <span className="response-task-due">{deadlineLabel(item.deadline_hours)}</span>
                  </li>
                );
              })}
            </ol>
          </article>
        ))}
      </div>
    </section>
  );
}

function StrategySection({ report }) {
  const strategies = report?.strategies ?? [];
  if (!strategies.length) return null;
  return (
    <section className="response-strategy-panel">
      <header className="response-section-heading">
        <div>
          <span>대응 전략</span>
          <h4>실행 원칙과 커뮤니케이션 방향</h4>
        </div>
      </header>
      <div className="response-strategy-grid">
        {strategies.map((strategy, index) => (
          <article key={`${strategy.title ?? "strategy"}-${index}`}>
            <div className="response-strategy-meta">
              <span>{STRATEGY_LABELS[strategy.strategy_type] ?? humanize(strategy.strategy_type)}</span>
              {strategy.target_stakeholder && <small>대상 · {strategy.target_stakeholder}</small>}
            </div>
            <h5>{strategy.title}</h5>
            <p>{strategy.detail}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function FollowUpSection({ report }) {
  const metrics = report?.monitoring_metrics ?? [];
  if (!metrics.length && !report?.limitations) return null;
  return (
    <section className="response-followup-panel">
      <header className="response-section-heading">
        <div>
          <span>후속 확인</span>
          <h4>대응 후 점검할 항목</h4>
        </div>
      </header>
      <div className="response-followup-grid">
        {metrics.length > 0 && (
          <article>
            <h5>모니터링 지표</h5>
            <div className="response-metric-chips">
              {metrics.map((metric, index) => (
                <span key={`${metric}-${index}`}>{metric}</span>
              ))}
            </div>
          </article>
        )}
        {report?.limitations && (
          <article className="response-caution-card">
            <h5>사용 전 확인</h5>
            <p>{report.limitations}</p>
          </article>
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
          <li key={`verification-${index}`}>
            {typeof violation === "string"
              ? violation
              : violation.message ?? "세부 검증 결과를 확인해 주세요."}
          </li>
        ))}
      </ul>
    </aside>
  );
}

export function NoEvidenceNotice({ content }) {
  const detection = content.detection ?? {};
  const probability =
    typeof detection.risk_probability === "number"
      ? `${Math.round(detection.risk_probability * 100)}%`
      : null;
  const facts = [
    ["탐지 위험도", probability],
    ["심각도", SEVERITY_LABELS[detection.severity] ?? humanize(detection.severity)],
  ].filter(([, value]) => value);

  return (
    <div className="response-draft response-draft-hold">
      <div className="response-hold-heading">
        <div>
          <span className="response-ui-kicker">대응안 생성 보류</span>
          <strong>연결된 근거 기사가 없습니다</strong>
        </div>
        <span>확인 필요</span>
      </div>
      <p>{content.review_reason || "위험 판단에 사용된 기사를 확인할 수 없습니다."}</p>
      {facts.length > 0 && (
        <dl>
          {facts.map(([name, value]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <small className="response-hold-guide">
        위험 근거 기사를 연결한 뒤 대응안을 다시 생성해 주세요.
      </small>
    </div>
  );
}

export default function MainResponseContent({ content }) {
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const initialIndex = Math.max(
    scenarios.findIndex((scenario) => scenario.stance === content.selected_stance),
    0
  );
  const [active, setActive] = useState(initialIndex);

  if (content.status === "근거부족_보류") {
    return <NoEvidenceNotice content={content} />;
  }

  const current = scenarios[active] ?? scenarios[0];
  const report = current?.report;

  return (
    <div className="response-draft response-draft-v3 response-operations-view">
      <ResponseHeader content={content} report={report} />
      <ScenarioSelector
        scenarios={scenarios}
        active={active}
        selected={content.selected_stance}
        onChange={setActive}
      />

      {current ? (
        <div className="response-plan-content" role="tabpanel">
          <SituationSection scenario={current} />
          <PlanSection report={report} />
          <StrategySection report={report} />
          <FollowUpSection report={report} />
          <VerificationNotice verification={current.verification} />
        </div>
      ) : (
        <p className="response-empty-state">생성된 대응안이 없습니다.</p>
      )}

    </div>
  );
}
