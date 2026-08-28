"""팀 저장소 환경에 맞춘 LLM·임베딩 호출 어댑터.

독립 CLI로 만들 때는 os.environ + chat.completions를 썼지만, 이 저장소는
  - 설정을 app.config.get_settings()로 주입하고
  - Responses API(responses.create + text.format)를 쓰며
  - 응답 모델이 gpt-5.6-luna로 temperature를 지원하지 않는다(실측: 400 Unsupported parameter)
는 세 가지가 다르다. 호출 지점마다 분기하지 않도록 이 파일에 모아 둔다.

**temperature를 안 쓰는데 시나리오가 갈리는 이유**: 시나리오는 같은 프롬프트를 여러 번
부르는 방식이 아니라 관점(stance)을 프롬프트에 명시해 방향을 벌린다. 그래서 temperature가
없어도 서로 다른 초안이 나온다(generate.SCENARIO_STANCES 참고).
"""
from __future__ import annotations

import json
from typing import Any

# 팀 저장소(app.config)와 독립 실행 양쪽에서 돌게 한다. 독립 실행은 원본 작업본을
# 그대로 돌려보기 위한 경로이고, 배포 환경에서는 항상 app.config 쪽이 잡힌다.
try:
    from app.config import get_settings

    _STANDALONE = False
except ImportError:  # pragma: no cover - 독립 실행 폴백
    import os

    from dotenv import load_dotenv

    load_dotenv()
    _STANDALONE = True

    class _EnvSettings:
        openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        response_model_name = os.environ.get("RISOTO_REPORT_MODEL", "gpt-5.4")
        embedding_model_name = os.environ.get("RISOTO_EMBED_MODEL", "text-embedding-3-small")

    def get_settings():
        return _EnvSettings()


def _client():
    from openai import OpenAI

    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("openai_api_key가 설정되어 있지 않습니다.")
    return OpenAI(api_key=settings.openai_api_key)


def response_model() -> str:
    return get_settings().response_model_name


def structured_call(
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
    model: str | None = None,
) -> tuple[dict, dict[str, int]]:
    """strict JSON 스키마로 한 번 호출한다. 반환: (파싱된 결과, 토큰 사용량).

    Responses API는 messages 대신 input을 받는다. 시스템·사용자 구분이 필요하므로
    역할이 드러나게 이어 붙인다.
    """
    client = _client()
    resp = client.responses.create(
        model=model or response_model(),
        input=f"{system}\n\n---\n\n{user}",
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    )
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 1}
    if getattr(resp, "usage", None):
        usage["input_tokens"] = resp.usage.input_tokens
        usage["output_tokens"] = resp.usage.output_tokens
    return json.loads(resp.output_text), usage


def embed_texts(texts: list[str]) -> tuple[list[list[float]], dict[str, int]]:
    """임베딩. 모델은 설정에 없으면 text-embedding-3-small을 쓴다."""
    settings = get_settings()
    model = getattr(settings, "embedding_model_name", "") or "text-embedding-3-small"
    client = _client()
    out: list[list[float]] = []
    usage = {"tokens": 0, "calls": 0}
    for start in range(0, len(texts), 64):
        resp = client.embeddings.create(model=model, input=texts[start : start + 64])
        out.extend(d.embedding for d in resp.data)
        usage["tokens"] += resp.usage.total_tokens
        usage["calls"] += 1
    return out, usage
