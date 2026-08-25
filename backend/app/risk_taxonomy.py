"""Stable identifiers for the reviewed multi-label corporate-risk taxonomy."""

RISK_TYPES = (
    "product_quality",
    "safety_accident",
    "security_privacy",
    "legal_regulatory",
    "labor_hr",
    "financial_governance",
    "supply_operations",
    "reputation_consumer",
)

# Human-confirmed false positives and pre-MVP imported candidates are retained
# for audit/training review, but are not operational risk events.
NON_REPORTABLE_RISK_STATUSES = ("dismissed", "legacy_candidate")
