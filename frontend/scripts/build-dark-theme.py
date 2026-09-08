"""styles.css에서 다크 테마 레이어를 생성한다.

이 프로젝트의 색은 대부분 토큰이 아니라 하드코딩된 hex다(색 선언 1050개 중
대다수). 그래서 토큰만 갈아끼우는 방식으로는 다크 테마가 성립하지 않는다.
대신 원본의 모든 색 선언을 읽어 `[data-theme="dark"]` 스코프 아래로 다시
써낸다. 생성물은 styles.css 끝의 배너 블록으로만 들어가므로

  - 웜(기본) 테마는 한 줄도 바뀌지 않고,
  - 규칙이 하나 틀려도 다크 테마에서만 티가 나며,
  - 배너 블록을 지우면 그대로 원상복구된다.

색 변환 규칙은 map_color()에 모여 있다. 팔레트를 바꾸고 싶으면 거기만 고치고
다시 실행하면 된다:  python scripts/build-dark-theme.py
"""

import colorsys
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CSS = os.path.join(HERE, "..", "src", "styles.css")

BEGIN = "/* ===== BEGIN GENERATED DARK THEME"
END = "/* ===== END GENERATED DARK THEME ===== */"

# ── 다크 팔레트 ──────────────────────────────────────────────────────────────
# 캔바 시안의 슬레이트 계열을 그대로 쓴다. 강조색만 파랑이 아니라 웜으로 둔다.
# 이 코드베이스에서 테라코타는 "강조"와 "위험"을 동시에 뜻하고 hex만으로는
# 둘을 구분할 수 없다. 파랑으로 옮기면 긴급 경보가 안내문처럼 읽힌다.
HUE_NEUTRAL = 217 / 360.0   # slate
HUE_WARM = 16 / 360.0       # coral - 강조와 위험
HUE_GOLD = 38 / 360.0       # amber - 주의
HUE_GREEN = 158 / 360.0     # emerald - 관찰/정상
HUE_COOL = 210 / 360.0

COLOR_PROPS = re.compile(
    r"^(color|background|background-color|border|border-[a-z]+|border-[a-z]+-color|"
    r"outline|outline-color|fill|stroke|box-shadow|text-shadow|caret-color|"
    r"column-rule|text-decoration-color|accent-color)$"
)


def hex_to_rgb(text):
    text = text.lstrip("#")
    if len(text) in (3, 4):
        text = "".join(c * 2 for c in text)
    alpha = 1.0
    if len(text) == 8:
        alpha = int(text[6:8], 16) / 255.0
        text = text[:6]
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), alpha)


def to_css(r, g, b, a=1.0):
    r, g, b = (max(0, min(255, int(round(v)))) for v in (r, g, b))
    if a >= 0.999:
        return "#%02x%02x%02x" % (r, g, b)
    return "rgba(%d, %d, %d, %s)" % (r, g, b, ("%.3f" % a).rstrip("0").rstrip("."))


def hls_to_css(h, l, s, a=1.0):
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return to_css(r * 255, g * 255, b * 255, a)


def family_of(hue_deg, chroma):
    """색 계열을 가른다.

    HLS 채도는 흰색과 검정 근처에서 부풀려져서 쓸 수 없다. 크림색 #f8f5ec은
    RGB 폭이 12밖에 안 되는데 채도는 0.46으로 나온다. 그래서 절대 채도(chroma,
    max-min)로 판단한다. 이 팔레트의 무채색은 전부 따뜻한 쪽(20~55도)에
    몰려 있으므로, 그 범위에서는 chroma 기준을 넉넉히 잡아야 진한 갈색 잉크
    #2e1f14까지 무채색으로 잡힌다.
    """
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


def map_color(r, g, b, a, role):
    """원본 색 하나를 다크 테마용 색으로 옮긴다.

    role은 그 색이 무슨 일을 하는지다. 같은 갈색이라도 글자면 밝아져야 하고
    배경이면 어두워져야 하므로, 명도를 뒤집는 방향이 role에 따라 갈린다.
    """
    hue, light, sat = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    hue_deg = hue * 360
    chroma = (max(r, g, b) - min(r, g, b)) / 255.0
    family = family_of(hue_deg, chroma)

    if role == "shadow":
        # 크림 위의 은은한 갈색 그림자는 어두운 바탕에서 보이지 않는다.
        return to_css(0, 0, 0, min(0.55, max(0.22, a * 3.2)))

    if family == "neutral":
        if role == "text":
            if light > 0.62:
                # 이미 밝은 글자는 컬러 배지나 버튼 위에 얹힌 흰 글자다.
                # 뒤집으면 배경까지 같이 어두워져서 서로 묻힌다. 밝은 채로 둔다.
                return hls_to_css(HUE_NEUTRAL, 0.93, 0.10, a)
            # 진한 잉크일수록 밝게. 흐린 회색은 흐린 채로 둔다.
            return hls_to_css(HUE_NEUTRAL, min(0.95, max(0.55, 1.0 - light * 0.62)), 0.16, a)
        if role == "background":
            # 밝을수록 살짝 더 밝은 어두운 면으로. 순서를 뒤집지 않아야
            # 원래 눌려 보이던 칩이 다크에서도 눌려 보인다.
            if light >= 0.5:
                new_l = 0.12 + (light - 0.5) * 0.24
            else:
                new_l = 0.30 + (0.5 - light) * 0.20
            return hls_to_css(HUE_NEUTRAL, new_l, 0.30, a)
        # 선
        return hls_to_css(HUE_NEUTRAL, 0.34, 0.20, a)

    hue_map = {"warm": HUE_WARM, "gold": HUE_GOLD, "green": HUE_GREEN, "cool": HUE_COOL}
    target_hue = hue_map[family]

    if role == "text":
        return hls_to_css(target_hue, 0.70, 0.72, a)
    if role == "background":
        # 배지 바탕: 색은 알아보되 글자가 얹힐 만큼 어둡게.
        return hls_to_css(target_hue, 0.26 if light > 0.6 else 0.30, 0.42, a)
    return hls_to_css(target_hue, 0.48, 0.52, a)


def role_for(prop, value):
    if prop.startswith("--"):
        # 커스텀 속성은 이름으로 쓰임새를 짐작한다. --panel-surface처럼 :root
        # 밖에서 다시 정의되는 토큰이 있어서 이쪽도 반드시 변환해야 한다.
        if "shadow" in prop:
            return "shadow"
        if any(key in prop for key in ("surface", "bg", "background", "soft", "paper", "cream", "rice")):
            return "background"
        if any(key in prop for key in ("border", "divider", "ring", "focus", "outline")):
            return "border"
        return "text"
    if "shadow" in prop:
        return "shadow"
    if prop in ("color", "fill", "stroke", "caret-color", "text-decoration-color"):
        return "text"
    if prop.startswith("background"):
        return "background"
    if prop.startswith("border") or prop.startswith("outline") or prop == "column-rule":
        return "border"
    return "text"


HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RGBA = re.compile(r"rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*(?:,\s*([0-9.]+)\s*)?\)")


def transform_value(prop, value):
    """선언 값 안의 모든 색을 바꾼다. 색이 없으면 None."""
    role = role_for(prop, value)
    found = [False]

    def swap_hex(match):
        found[0] = True
        r, g, b, a = hex_to_rgb(match.group(0))
        return map_color(r, g, b, a, role)

    def swap_rgba(match):
        found[0] = True
        r, g, b = (float(match.group(i)) for i in (1, 2, 3))
        a = float(match.group(4)) if match.group(4) else 1.0
        return map_color(r, g, b, a, role)

    out = HEX.sub(swap_hex, value)
    out = RGBA.sub(swap_rgba, out)
    if re.search(r"\bwhite\b", out):
        found[0] = True
        out = re.sub(r"\bwhite\b", "#e2e8f0" if role == "text" else "#1e293b", out)
    return out if found[0] else None


def scope(selector):
    """선택자를 다크 스코프 아래로 넣는다. 항상 특이도가 한 단계 올라가므로
    원본 규칙을 반드시 이긴다."""
    parts = [p.strip() for p in selector.split(",") if p.strip()]
    scoped = []
    for part in parts:
        if part in (":root", "html", "body") or part.startswith(":root"):
            scoped.append(part.replace(":root", "", 1).strip() and
                          '[data-theme="dark"]' + part[len(":root"):] or '[data-theme="dark"]')
        elif part.startswith("html") or part.startswith("body"):
            scoped.append('[data-theme="dark"]' + part[4:])
        else:
            scoped.append('[data-theme="dark"] ' + part)
    return ", ".join(scoped)


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def parse(text):
    """(at_rule_stack, selector, [(prop, value, important)]) 목록을 만든다."""
    rules = []
    i = 0
    stack = []
    buffer = ""
    length = len(text)
    while i < length:
        char = text[i]
        if char == "{":
            head = buffer.strip()
            buffer = ""
            if head.startswith("@"):
                stack.append(head)
                i += 1
                if re.match(r"@(keyframes|font-face|property|counter-style)", head):
                    # 안쪽은 선택자가 아니므로 통째로 건너뛴다.
                    depth = 1
                    while i < length and depth:
                        if text[i] == "{":
                            depth += 1
                        elif text[i] == "}":
                            depth -= 1
                        i += 1
                    stack.pop()
                continue
            # 일반 규칙
            depth = 1
            start = i + 1
            i += 1
            while i < length and depth:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            body = text[start:i]
            i += 1
            decls = []
            for piece in body.split(";"):
                if ":" not in piece:
                    continue
                prop, _, value = piece.partition(":")
                prop = prop.strip().lower()
                value = value.strip()
                important = False
                if value.endswith("!important"):
                    value = value[: -len("!important")].strip()
                    important = True
                if prop and value:
                    decls.append((prop, value, important))
            if decls:
                rules.append((list(stack), head, decls))
            continue
        if char == "}":
            if stack:
                stack.pop()
            buffer = ""
            i += 1
            continue
        buffer += char
        i += 1
    return rules


def main():
    raw = io.open(CSS, encoding="utf-8").read()
    if BEGIN in raw:
        raw = raw[: raw.index(BEGIN)].rstrip() + "\n"

    rules = parse(strip_comments(raw))

    generated = []
    count = 0
    for at_stack, selector, decls in rules:
        head = selector.strip()
        # :root 토큰은 손으로 정의한다. 이미 다크 스코프가 붙은 규칙(손으로 쓴
        # 다크 토큰 블록)을 다시 변환하면 자기 자신을 덮어써서 테마가 깨진다.
        if head.startswith(":root") or "[data-theme=" in head:
            continue
        out = []
        for prop, value, important in decls:
            # 커스텀 속성은 :root 밖에서 재정의된 것만 다룬다(:root는 손으로 정의).
            if not prop.startswith("--") and not COLOR_PROPS.match(prop):
                continue
            if "var(--" in value and not HEX.search(value) and not RGBA.search(value):
                continue  # 토큰만 쓰는 선언은 토큰이 바뀌면 따라온다
            mapped = transform_value(prop, value)
            if mapped is None or mapped == value:
                continue
            out.append("%s: %s%s;" % (prop, mapped, " !important" if important else ""))
            count += 1
        if not out:
            continue
        rule = "%s { %s }" % (scope(selector), " ".join(out))
        for at in reversed(at_stack):
            rule = "%s { %s }" % (at, rule)
        generated.append(rule)

    banner = (
        BEGIN + " =====\n"
        "   scripts/build-dark-theme.py가 만든 블록입니다. 직접 고치지 마세요.\n"
        "   원본 규칙의 색만 다크용으로 다시 계산해 [data-theme=\"dark\"] 아래에\n"
        "   깔아둔 것이라, 이 블록을 지우면 웜 테마만 남습니다.\n"
        "   팔레트를 바꾸려면 스크립트의 map_color()를 고치고 다시 실행하세요.\n"
        "   ===== */\n"
    )
    block = "\n\n" + banner + "\n".join(generated) + "\n" + END + "\n"
    io.open(CSS, "w", encoding="utf-8").write(raw + block)
    print("규칙 %d개 / 선언 %d개 생성" % (len(generated), count))
    print("추가된 용량: %.1f KB" % (len(block) / 1024))


if __name__ == "__main__":
    main()
