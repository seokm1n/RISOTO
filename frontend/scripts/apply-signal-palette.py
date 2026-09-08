"""styles.css의 나머지 화면(파이프라인·관리·로그인·마이페이지·관리자)을 브리핑
홈/위험 상세에서 먼저 쓰던 "브리핑룸" 팔레트로 옮긴다.

:root 토큰은 이미 손으로 새 값으로 바꿔 두었다(729회 참조되는 CSS 변수라
그것만으로 상당 부분이 자동으로 따라온다). 문제는 토큰을 안 쓰고 각 컴포넌트에
직접 박아 넣은 하드코딩 hex다(선언 1050개 중 다수). 이 스크립트는 그 하드코딩
색만 골라 같은 밝기·채도를 유지한 채 색상만 새 팔레트 계열로 반사시킨다.

다크 테마 생성기(build-dark-theme.py)와의 차이: 그쪽은 라이트→다크로 밝기를
"뒤집는" 작업이라 속성 역할(텍스트/배경/테두리)이 중요했다. 이번은 라이트→
라이트로 색상 계열만 바꾸는 작업이라 밝기는 그대로 두면 되고, 그래서 역할
구분 없이 모든 hex를 같은 규칙으로 처리해도 안전하다.

건드리지 않는 부분(이미 손으로 맞춰 둔 곳):
  - 파일 맨 위 기본 :root
  - "브리핑룸 테마 레이어" :root (브랜드 토큰)
  - :root[data-theme="dark"] 블록과 그 바로 아래 다크 전용 보정 두 줄
  - 파일 끝의 .signal-scope 블록(처음부터 새 팔레트로 지었다)
  - 자동 생성된 다크 테마 블록(재실행해서 새로 만든다)

실행: python scripts/apply-signal-palette.py
이후 build-dark-theme.py를 다시 돌려 다크 테마를 새 색 기준으로 재생성한다
(이 스크립트가 자동으로 이어서 호출한다).
"""

import colorsys
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSS = os.path.join(HERE, "..", "src", "styles.css")

BRAND_ROOT_START = "/* ═══════════════════════════════════════════════════════════════════════════\n   브리핑룸 테마 레이어"
DARK_ROOT_START = "/* ═══════════════════════════════════════════════════════════════════════════\n   다크 테마 토큰"
GENERATED_START = "/* ===== BEGIN GENERATED DARK THEME"
GENERATED_END_MARK = "===== END GENERATED DARK THEME ===== */"

# 새 팔레트에서 색상 계열별 대표 hue를 실제 hex로부터 계산한다(손으로 각도를
# 적어 넣지 않고 실물 색에서 뽑아 써야 나중에 팔레트를 바꿔도 어긋나지 않는다).
HUE_SOURCE = {
    "neutral": "#223059",  # 남색 강조 - 무채색 계열이 향할 방향
    "warm": "#b3323f",     # 크림슨 - 기존 테라코타(강조+위험) 계열이 향할 방향
    "gold": "#b0731a",     # 앰버 - 기존 골드(주의) 계열이 향할 방향
    "green": "#29715f",    # 틸 - 기존 세이지(안전) 계열이 향할 방향
}


def hex_to_rgb(text):
    text = text.lstrip("#")
    if len(text) in (3, 4):
        text = "".join(c * 2 for c in text)
    alpha = 1.0
    if len(text) == 8:
        alpha = int(text[6:8], 16) / 255.0
        text = text[:6]
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), alpha)


def hue_of(hex_value):
    r, g, b, _ = hex_to_rgb(hex_value)
    h, _, _ = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return h


HUE_TARGET = {family: hue_of(hexv) for family, hexv in HUE_SOURCE.items()}


def to_css(r, g, b, a=1.0):
    r, g, b = (max(0, min(255, int(round(v)))) for v in (r, g, b))
    if a >= 0.999:
        return "#%02x%02x%02x" % (r, g, b)
    return "rgba(%d, %d, %d, %s)" % (r, g, b, ("%.3f" % a).rstrip("0").rstrip("."))


def hls_to_css(h, l, s, a=1.0):
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return to_css(r * 255, g * 255, b * 255, a)


def family_of(hue_deg, chroma):
    """build-dark-theme.py와 같은 분류 기준. 무채색 판정에 절대 채도(chroma)를
    쓰는 이유도 동일하다 - HLS 채도는 흰색·검정 근처에서 실제보다 부풀려진다."""
    if chroma < 0.06:
        return "neutral"
    if 20 <= hue_deg < 55 and chroma < 0.20:
        return "neutral"
    if hue_deg >= 340 or hue_deg < 20:
        return "warm"
    if hue_deg < 70:
        return "gold"
    if hue_deg < 200:
        return "green"
    return "cool"


def reflect(r, g, b, a):
    """밝기·채도는 그대로 두고 색상 계열만 새 팔레트 방향으로 반사시킨다."""
    hue, light, sat = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    hue_deg = hue * 360
    chroma = (max(r, g, b) - min(r, g, b)) / 255.0
    family = family_of(hue_deg, chroma)
    if family == "cool":
        return None  # 원래 팔레트에 거의 없던 계열 - 손대지 않는다
    if family == "neutral":
        # 무채색은 색상 계열을 바꾸되 채도를 낮게 눌러 정말 무채색으로 남긴다.
        return hls_to_css(HUE_TARGET["neutral"], light, min(sat, 0.10), a)
    return hls_to_css(HUE_TARGET[family], light, sat, a)


HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RGBA = re.compile(r"rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*(?:,\s*([0-9.]+)\s*)?\)")


def recolor_chunk(text):
    changed = [0]

    def swap_hex(match):
        r, g, b, a = hex_to_rgb(match.group(0))
        out = reflect(r, g, b, a)
        if out is None:
            return match.group(0)
        changed[0] += 1
        return out

    def swap_rgba(match):
        r, g, b = (float(match.group(i)) for i in (1, 2, 3))
        a = float(match.group(4)) if match.group(4) else 1.0
        out = reflect(r, g, b, a)
        if out is None:
            return match.group(0)
        changed[0] += 1
        return out

    text = HEX.sub(swap_hex, text)
    text = RGBA.sub(swap_rgba, text)
    return text, changed[0]


def main():
    raw = io.open(CSS, encoding="utf-8").read()

    if GENERATED_START not in raw:
        print("생성된 다크 블록을 찾지 못했습니다. 파일 구조를 확인하세요.")
        sys.exit(1)
    head_all, _, rest = raw.partition(GENERATED_START)
    if GENERATED_END_MARK not in rest:
        print("다크 블록 종료 표시를 찾지 못했습니다.")
        sys.exit(1)
    _, _, tail = rest.partition(GENERATED_END_MARK)
    tail = tail.lstrip("\n")  # .signal-scope 블록 등, 그대로 보존한다

    if BRAND_ROOT_START not in head_all or DARK_ROOT_START not in head_all:
        print("브랜드 :root 또는 다크 :root 블록을 찾지 못했습니다.")
        sys.exit(1)
    before_brand, _, after_brand_start = head_all.partition(BRAND_ROOT_START)
    # 브랜드 :root 블록의 끝(첫 "}\n\n")을 찾는다.
    brand_body_end = after_brand_start.index("}\n") + 2
    brand_block = BRAND_ROOT_START + after_brand_start[:brand_body_end]
    after_brand = after_brand_start[brand_body_end:]

    if DARK_ROOT_START not in after_brand:
        print("다크 :root 블록 위치가 예상과 다릅니다.")
        sys.exit(1)
    middle, _, after_dark_start = after_brand.partition(DARK_ROOT_START)
    # 다크 :root 블록 + 바로 아래 손으로 쓴 보정 두 규칙까지 통째로 보존한다.
    # 다음 섹션 시작 표시(생성 블록)까지가 전부 [data-theme="dark"] 스코프다.
    dark_block = DARK_ROOT_START + after_dark_start

    recolored_before, n1 = recolor_chunk(before_brand)
    recolored_middle, n2 = recolor_chunk(middle)
    total = n1 + n2
    print("재색칠한 색상 선언: %d개" % total)

    new_head = recolored_before + brand_block + recolored_middle + dark_block
    io.open(CSS, "w", encoding="utf-8").write(new_head + tail)
    print("생성된 다크 블록을 지우고 build-dark-theme.py를 다시 실행합니다...")

    result = subprocess.run([sys.executable, os.path.join(HERE, "build-dark-theme.py")])
    if result.returncode != 0:
        print("다크 테마 재생성 실패 - styles.css를 확인하세요.")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
