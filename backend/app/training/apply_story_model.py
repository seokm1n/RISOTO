"""Re-score operational stories with a checksum-pinned artifact, preserving an audit backup.

python -m app.training.apply_story_model --artifact /app/model_artifacts/...joblib --sha256 ... --apply
"""
import argparse
import json

from app.config import get_settings
from app.services.story_model_runtime import resolve_story_risk_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact")
    parser.add_argument("--sha256")
    parser.add_argument("--company-id", type=int)
    parser.add_argument("--output")
    parser.add_argument("--apply", action="store_true", help="Persist predictions/events; default only validates model")
    args = parser.parse_args()
    updates = {"story_risk_model_enabled": True, "story_risk_engine_enabled": True}
    if args.artifact:
        updates["story_risk_model_path"] = args.artifact
    if args.sha256:
        updates["story_risk_model_sha256"] = args.sha256
    settings = get_settings().model_copy(update=updates)
    runtime = resolve_story_risk_runtime(settings)
    if not runtime.available:
        raise SystemExit(runtime.message)
    if not args.apply:
        print(json.dumps(dict(available=True, version=runtime.version, threshold=runtime.threshold,
                             artifact_sha256=runtime.artifact_sha256, applied=False)))
        return
    from app.services.story_model_backfill import reapply_story_model

    result = reapply_story_model(company_id=args.company_id, output_dir=args.output, settings=settings)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
