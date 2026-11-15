"""
디자인 토큰 - 예약 창 색상·모양 값 관리.

테마 하나 = 기본 토큰 8개 + 동작별 토큰 6개 × 3(종료/재시작/모니터).
화면 코드는 토큰 이름으로만 색을 참조하므로, 테마를 늘릴 때는 THEMES 에 값만 더한다.
"""

import copy
from typing import Any, Optional

from cfg.config import (
    ACTION_MONITOR_OFF,
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    FOLLOW_SYSTEM_DEFAULT,
    SETTINGS_KEY_CONTRAST_FIX,
    SETTINGS_KEY_DARK_THEME,
    SETTINGS_KEY_FOLLOW_SYSTEM,
    SETTINGS_KEY_LIGHT_THEME,
    SETTINGS_KEY_THEME,
)

BASE_KEYS: tuple[str, ...] = (
    "background",
    "surface",
    "surfaceAlt",
    "border",
    "text",
    "textMuted",
    "success",
    "warning",
)
ACTION_KEYS: tuple[str, ...] = (
    "accent",
    "accentText",
    "tabFill",
    "onTab",
    "buttonFill",
    "onButton",
)
ACTION_NAMES: tuple[str, ...] = ("shutdown", "restart", "monitor")

# 모양 토큰 (모든 테마 공통, DPI 100% 기준 px) — 설계안 6장
SHAPE: dict[str, dict[str, int]] = {
    "radius": {"window": 12, "card": 12, "control": 10, "chip": 8},
    "ring": {"size": 196, "radius": 84, "stroke": 12, "handle": 14},
    "spacing": {"outer": 16, "gap": 12, "card": 14, "chip": 6},
}

DEFAULT_THEME_ID: str = "gh"
DEFAULT_DARK_THEME_ID: str = "gh"
DEFAULT_LIGHT_THEME_ID: str = "light"
# 시안 10종의 토글 손잡이가 모두 흰색임 — 토큰에 없어 모양 값으로 둠
SWITCH_KNOB: str = "#ffffff"

# 값은 설계안 p10~p16 표에서 기계 추출함 (가독성 보정 적용된 tokens)
THEMES: dict[str, dict[str, Any]] = {
    "gh": {
        "id": "gh",
        "name": "GitHub Dark",
        "mode": "dark",
        "tokens": {
            "background": "#0d1117",
            "surface": "#161b22",
            "surfaceAlt": "#21262d",
            "border": "#30363d",
            "text": "#e6edf3",
            "textMuted": "#8b949e",
            "success": "#3fb950",
            "warning": "#d29922",
            "actions": {
                "shutdown": {
                    "accent": "#ff7b72",
                    "accentText": "#ff7b72",
                    "tabFill": "#ff7b72",
                    "onTab": "#0d1117",
                    "buttonFill": "#d0443d",
                    "onButton": "#ffffff",
                },
                "restart": {
                    "accent": "#f0883e",
                    "accentText": "#f0883e",
                    "tabFill": "#f0883e",
                    "onTab": "#0d1117",
                    "buttonFill": "#b85c22",
                    "onButton": "#ffffff",
                },
                "monitor": {
                    "accent": "#58a6ff",
                    "accentText": "#58a6ff",
                    "tabFill": "#58a6ff",
                    "onTab": "#0d1117",
                    "buttonFill": "#1f6feb",
                    "onButton": "#ffffff",
                },
            },
        },
    },
    "tokyo": {
        "id": "tokyo",
        "name": "Tokyo Night",
        "mode": "dark",
        "tokens": {
            "background": "#1a1b26",
            "surface": "#1f2335",
            "surfaceAlt": "#292e42",
            "border": "#3b4261",
            "text": "#c0caf5",
            "textMuted": "#8e96bf",
            "success": "#9ece6a",
            "warning": "#d29922",
            "actions": {
                "shutdown": {
                    "accent": "#f7768e",
                    "accentText": "#f7768e",
                    "tabFill": "#f7768e",
                    "onTab": "#1a1b26",
                    "buttonFill": "#f7768e",
                    "onButton": "#1a1b26",
                },
                "restart": {
                    "accent": "#ff9e64",
                    "accentText": "#ff9e64",
                    "tabFill": "#ff9e64",
                    "onTab": "#1a1b26",
                    "buttonFill": "#ff9e64",
                    "onButton": "#1a1b26",
                },
                "monitor": {
                    "accent": "#7aa2f7",
                    "accentText": "#7aa2f7",
                    "tabFill": "#7aa2f7",
                    "onTab": "#1a1b26",
                    "buttonFill": "#7aa2f7",
                    "onButton": "#1a1b26",
                },
            },
        },
    },
    "rose": {
        "id": "rose",
        "name": "Rosé Pine",
        "mode": "dark",
        "tokens": {
            "background": "#191724",
            "surface": "#1f1d2e",
            "surfaceAlt": "#26233a",
            "border": "#403d52",
            "text": "#e0def4",
            "textMuted": "#908caa",
            "success": "#31748f",
            "warning": "#d29922",
            "actions": {
                "shutdown": {
                    "accent": "#eb6f92",
                    "accentText": "#eb6f92",
                    "tabFill": "#eb6f92",
                    "onTab": "#191724",
                    "buttonFill": "#eb6f92",
                    "onButton": "#191724",
                },
                "restart": {
                    "accent": "#f6c177",
                    "accentText": "#f6c177",
                    "tabFill": "#f6c177",
                    "onTab": "#191724",
                    "buttonFill": "#f6c177",
                    "onButton": "#191724",
                },
                "monitor": {
                    "accent": "#9ccfd8",
                    "accentText": "#9ccfd8",
                    "tabFill": "#9ccfd8",
                    "onTab": "#191724",
                    "buttonFill": "#9ccfd8",
                    "onButton": "#191724",
                },
            },
        },
    },
    "gruv": {
        "id": "gruv",
        "name": "Gruvbox",
        "mode": "dark",
        "tokens": {
            "background": "#282828",
            "surface": "#32302f",
            "surfaceAlt": "#3c3836",
            "border": "#504945",
            "text": "#ebdbb2",
            "textMuted": "#afa089",
            "success": "#b8bb26",
            "warning": "#d29922",
            "actions": {
                "shutdown": {
                    "accent": "#fb4934",
                    "accentText": "#f58366",
                    "tabFill": "#fb5440",
                    "onTab": "#282828",
                    "buttonFill": "#cc241d",
                    "onButton": "#fbf1c7",
                },
                "restart": {
                    "accent": "#fe8019",
                    "accentText": "#fe8019",
                    "tabFill": "#fe8019",
                    "onTab": "#282828",
                    "buttonFill": "#fe8019",
                    "onButton": "#282828",
                },
                "monitor": {
                    "accent": "#83a598",
                    "accentText": "#8daa9b",
                    "tabFill": "#83a598",
                    "onTab": "#282828",
                    "buttonFill": "#83a598",
                    "onButton": "#282828",
                },
            },
        },
    },
    "dim": {
        "id": "dim",
        "name": "GitHub Dimmed",
        "mode": "dark",
        "tokens": {
            "background": "#22272e",
            "surface": "#2d333b",
            "surfaceAlt": "#373e47",
            "border": "#444c56",
            "text": "#cdd9e5",
            "textMuted": "#9daab6",
            "success": "#57ab5a",
            "warning": "#d29922",
            "actions": {
                "shutdown": {
                    "accent": "#f47067",
                    "accentText": "#e8908d",
                    "tabFill": "#f47067",
                    "onTab": "#22272e",
                    "buttonFill": "#ca4942",
                    "onButton": "#ffffff",
                },
                "restart": {
                    "accent": "#e0823d",
                    "accentText": "#da9c6f",
                    "tabFill": "#e0823d",
                    "onTab": "#22272e",
                    "buttonFill": "#e0823d",
                    "onButton": "#22272e",
                },
                "monitor": {
                    "accent": "#539bf5",
                    "accentText": "#72abf1",
                    "tabFill": "#539bf5",
                    "onTab": "#22272e",
                    "buttonFill": "#539bf5",
                    "onButton": "#22272e",
                },
            },
        },
    },
    "one": {
        "id": "one",
        "name": "One Dark",
        "mode": "dark",
        "tokens": {
            "background": "#282c34",
            "surface": "#21252b",
            "surfaceAlt": "#2c313a",
            "border": "#3e4451",
            "text": "#abb2bf",
            "textMuted": "#9399a4",
            "success": "#98c379",
            "warning": "#d29922",
            "actions": {
                "shutdown": {
                    "accent": "#e06c75",
                    "accentText": "#cd858f",
                    "tabFill": "#e06c75",
                    "onTab": "#1e2127",
                    "buttonFill": "#e06c75",
                    "onButton": "#1e2127",
                },
                "restart": {
                    "accent": "#d19a66",
                    "accentText": "#d19a66",
                    "tabFill": "#d19a66",
                    "onTab": "#1e2127",
                    "buttonFill": "#d19a66",
                    "onButton": "#1e2127",
                },
                "monitor": {
                    "accent": "#61afef",
                    "accentText": "#61afef",
                    "tabFill": "#61afef",
                    "onTab": "#1e2127",
                    "buttonFill": "#61afef",
                    "onButton": "#1e2127",
                },
            },
        },
    },
    "cat": {
        "id": "cat",
        "name": "Catppuccin Mocha",
        "mode": "dark",
        "tokens": {
            "background": "#1e1e2e",
            "surface": "#181825",
            "surfaceAlt": "#313244",
            "border": "#45475a",
            "text": "#cdd6f4",
            "textMuted": "#969cb5",
            "success": "#a6e3a1",
            "warning": "#d29922",
            "actions": {
                "shutdown": {
                    "accent": "#f38ba8",
                    "accentText": "#f38ba8",
                    "tabFill": "#f38ba8",
                    "onTab": "#1e1e2e",
                    "buttonFill": "#f38ba8",
                    "onButton": "#1e1e2e",
                },
                "restart": {
                    "accent": "#fab387",
                    "accentText": "#fab387",
                    "tabFill": "#fab387",
                    "onTab": "#1e1e2e",
                    "buttonFill": "#fab387",
                    "onButton": "#1e1e2e",
                },
                "monitor": {
                    "accent": "#89b4fa",
                    "accentText": "#89b4fa",
                    "tabFill": "#89b4fa",
                    "onTab": "#1e1e2e",
                    "buttonFill": "#89b4fa",
                    "onButton": "#1e1e2e",
                },
            },
        },
    },
    "nord": {
        "id": "nord",
        "name": "Nord",
        "mode": "dark",
        "tokens": {
            "background": "#2e3440",
            "surface": "#3b4252",
            "surfaceAlt": "#434c5e",
            "border": "#4c566a",
            "text": "#eceff4",
            "textMuted": "#b7becb",
            "success": "#a3be8c",
            "warning": "#d29922",
            "actions": {
                "shutdown": {
                    "accent": "#bf616a",
                    "accentText": "#dab6bd",
                    "tabFill": "#b45b64",
                    "onTab": "#ffffff",
                    "buttonFill": "#b45b64",
                    "onButton": "#ffffff",
                },
                "restart": {
                    "accent": "#d08770",
                    "accentText": "#ddb6ab",
                    "tabFill": "#d28c76",
                    "onTab": "#2e3440",
                    "buttonFill": "#d28c76",
                    "onButton": "#2e3440",
                },
                "monitor": {
                    "accent": "#88c0d0",
                    "accentText": "#92c5d4",
                    "tabFill": "#88c0d0",
                    "onTab": "#2e3440",
                    "buttonFill": "#88c0d0",
                    "onButton": "#2e3440",
                },
            },
        },
    },
    "drac": {
        "id": "drac",
        "name": "Dracula",
        "mode": "dark",
        "tokens": {
            "background": "#282a36",
            "surface": "#21222c",
            "surfaceAlt": "#343746",
            "border": "#44475a",
            "text": "#f8f8f2",
            "textMuted": "#9ba2cb",
            "success": "#50fa7b",
            "warning": "#d29922",
            "actions": {
                "shutdown": {
                    "accent": "#ff5555",
                    "accentText": "#fe7674",
                    "tabFill": "#ff5555",
                    "onTab": "#282a36",
                    "buttonFill": "#cc4444",
                    "onButton": "#ffffff",
                },
                "restart": {
                    "accent": "#ffb86c",
                    "accentText": "#ffb86c",
                    "tabFill": "#ffb86c",
                    "onTab": "#282a36",
                    "buttonFill": "#ffb86c",
                    "onButton": "#282a36",
                },
                "monitor": {
                    "accent": "#bd93f9",
                    "accentText": "#bd93f9",
                    "tabFill": "#bd93f9",
                    "onTab": "#282a36",
                    "buttonFill": "#bd93f9",
                    "onButton": "#282a36",
                },
            },
        },
    },
    "light": {
        "id": "light",
        "name": "GitHub Light",
        "mode": "light",
        "tokens": {
            "background": "#ffffff",
            "surface": "#f6f8fa",
            "surfaceAlt": "#eaeef2",
            "border": "#d0d7de",
            "text": "#1f2328",
            "textMuted": "#626972",
            "success": "#1a7f37",
            "warning": "#9a6700",
            "actions": {
                "shutdown": {
                    "accent": "#cf222e",
                    "accentText": "#cf222e",
                    "tabFill": "#cf222e",
                    "onTab": "#ffffff",
                    "buttonFill": "#cf222e",
                    "onButton": "#ffffff",
                },
                "restart": {
                    "accent": "#bc4c00",
                    "accentText": "#b44a02",
                    "tabFill": "#bc4c00",
                    "onTab": "#ffffff",
                    "buttonFill": "#bc4c00",
                    "onButton": "#ffffff",
                },
                "monitor": {
                    "accent": "#0969da",
                    "accentText": "#0a66d1",
                    "tabFill": "#0969da",
                    "onTab": "#ffffff",
                    "buttonFill": "#0969da",
                    "onButton": "#ffffff",
                },
            },
        },
    },
}

# 가독성 보정을 끌 때 되돌릴 원본값 (점 경로 → 원본 색). 설계안 8장에 기록된 39경로뿐임
PALETTE_OVERRIDES: dict[str, dict[str, str]] = {
    "gh": {
        "actions.shutdown.onTab": "#ffffff",
        "actions.shutdown.buttonFill": "#f85149",
        "actions.restart.onTab": "#ffffff",
        "actions.restart.buttonFill": "#db6d28",
        "actions.monitor.onTab": "#ffffff",
    },
    "tokyo": {"textMuted": "#737aa2"},
    "rose": {},
    "gruv": {
        "textMuted": "#a89984",
        "actions.shutdown.tabFill": "#fb4934",
        "actions.shutdown.accentText": "#fb4934",
        "actions.monitor.accentText": "#83a598",
    },
    "dim": {
        "textMuted": "#768390",
        "actions.shutdown.onTab": "#ffffff",
        "actions.shutdown.buttonFill": "#e5534b",
        "actions.restart.onTab": "#ffffff",
        "actions.monitor.onTab": "#ffffff",
        "actions.shutdown.accentText": "#f47067",
        "actions.restart.accentText": "#e0823d",
        "actions.monitor.accentText": "#539bf5",
    },
    "one": {"textMuted": "#7f848e", "actions.shutdown.accentText": "#e06c75"},
    "cat": {"textMuted": "#9399b2"},
    "nord": {
        "textMuted": "#9aa3b5",
        "actions.shutdown.tabFill": "#bf616a",
        "actions.shutdown.buttonFill": "#bf616a",
        "actions.restart.tabFill": "#d08770",
        "actions.restart.onTab": "#ffffff",
        "actions.restart.buttonFill": "#d08770",
        "actions.restart.onButton": "#ffffff",
        "actions.shutdown.accentText": "#bf616a",
        "actions.restart.accentText": "#d08770",
        "actions.monitor.accentText": "#88c0d0",
    },
    "drac": {
        "textMuted": "#8b93c4",
        "actions.shutdown.onTab": "#ffffff",
        "actions.shutdown.buttonFill": "#ff5555",
        "actions.restart.onTab": "#ffffff",
        "actions.shutdown.accentText": "#ff5555",
    },
    "light": {
        "textMuted": "#656d76",
        "actions.restart.accentText": "#bc4c00",
        "actions.monitor.accentText": "#0969da",
    },
}

_ACTION_TO_NAME: dict[str, str] = {
    ACTION_SHUTDOWN: "shutdown",
    ACTION_RESTART: "restart",
    ACTION_MONITOR_OFF: "monitor",
}


def _is_hex_color(value: object) -> bool:
    """'#rrggbb' 형식 문자열인지 확인한다.

    Args:
        value: 검사할 값

    Returns:
        형식이 맞으면 True
    """
    if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True


def validate_theme(theme: object) -> bool:
    """테마 사전에 필수 토큰이 모두 있고 색 형식이 맞는지 확인한다.

    Args:
        theme: THEMES 의 값과 같은 구조의 사전

    Returns:
        쓸 수 있는 테마면 True
    """
    if not isinstance(theme, dict) or theme.get("mode") not in ("dark", "light"):
        return False
    tokens = theme.get("tokens")
    if not isinstance(tokens, dict):
        return False
    if not all(_is_hex_color(tokens.get(key)) for key in BASE_KEYS):
        return False
    actions = tokens.get("actions")
    if not isinstance(actions, dict):
        return False
    for name in ACTION_NAMES:
        group = actions.get(name)
        if not isinstance(group, dict):
            return False
        if not all(_is_hex_color(group.get(key)) for key in ACTION_KEYS):
            return False
    return True


def get_theme(
    theme_id: str = DEFAULT_THEME_ID, contrast_fix: bool = True
) -> dict[str, Any]:
    """id 로 테마를 찾는다. 없거나 깨졌으면 기본 테마를 돌려준다.

    Args:
        theme_id: 테마 id
        contrast_fix: False 면 가독성 보정 전 원본값으로 되돌린 복사본을 돌려줌

    Returns:
        테마 사전
    """
    theme = THEMES.get(theme_id)
    if theme is None or not validate_theme(theme):
        theme = THEMES[DEFAULT_THEME_ID]
    if contrast_fix:
        return theme
    return {**theme, "tokens": _restore_palette(theme)}


def _restore_palette(theme: dict[str, Any]) -> dict[str, Any]:
    """토큰 복사본에 8장 기록의 원본값을 덮어쓴다. 기록 없는 값은 토큰과 같게 둔다.

    Args:
        theme: 테마 사전

    Returns:
        원본값을 덮어쓴 tokens 복사본
    """
    tokens = copy.deepcopy(theme["tokens"])
    for path, color in PALETTE_OVERRIDES.get(theme["id"], {}).items():
        *parents, leaf = path.split(".")
        node = tokens
        for part in parents:
            node = node[part]
        node[leaf] = color
    return tokens


def theme_ids(mode: str) -> list[str]:
    """모드에 맞는 테마 id 목록을 돌려준다 (드롭다운 목록).

    Args:
        mode: "dark" 또는 "light"

    Returns:
        테마 id 목록
    """
    return [tid for tid, theme in THEMES.items() if theme["mode"] == mode]


def swatch_colors(theme: dict[str, Any]) -> tuple[str, str, str, str]:
    """썸네일 스와치 4색을 돌려준다.

    Args:
        theme: 테마 사전

    Returns:
        (background, surface, 종료 accent, 모니터 accent)
    """
    tokens = theme["tokens"]
    return (
        tokens["background"],
        tokens["surface"],
        tokens["actions"]["shutdown"]["accent"],
        tokens["actions"]["monitor"]["accent"],
    )


def clean_theme_settings(data: dict[str, Any]) -> dict[str, Any]:
    """쓸 수 있는 키만 남긴다. 없거나 틀린 키는 빼서 기본값을 쓰게 한다.

    Args:
        data: settings.json 에서 읽은 사전

    Returns:
        검증을 통과한 테마 설정 키만 담은 사전
    """
    clean: dict[str, Any] = {}
    for key in (SETTINGS_KEY_FOLLOW_SYSTEM, SETTINGS_KEY_CONTRAST_FIX):
        if isinstance(data.get(key), bool):
            clean[key] = data[key]
    for key, mode in (
        (SETTINGS_KEY_THEME, None),
        (SETTINGS_KEY_DARK_THEME, "dark"),
        (SETTINGS_KEY_LIGHT_THEME, "light"),
    ):
        value = data.get(key)
        # 목록·사전은 해시가 안 돼 in 검사 전에 문자열만 거름
        if (
            isinstance(value, str)
            and value in THEMES
            and mode in (None, THEMES[value]["mode"])
        ):
            clean[key] = value
    return clean


def resolve_theme_id(settings: dict[str, Any], system_light: Optional[bool]) -> str:
    """설정과 시스템 테마로 쓸 테마 id 를 정한다.

    Args:
        settings: 사용자 설정
        system_light: 시스템이 라이트면 True, 읽을 수 없으면 None(다크로 봄)

    Returns:
        테마 id
    """
    if settings.get(SETTINGS_KEY_FOLLOW_SYSTEM, FOLLOW_SYSTEM_DEFAULT):
        if system_light:
            return settings.get(SETTINGS_KEY_LIGHT_THEME, DEFAULT_LIGHT_THEME_ID)
        return settings.get(SETTINGS_KEY_DARK_THEME, DEFAULT_DARK_THEME_ID)
    return settings.get(SETTINGS_KEY_THEME, DEFAULT_THEME_ID)


def action_tokens(theme: dict[str, Any], action: str) -> dict[str, str]:
    """동작에 맞는 동작별 토큰 묶음을 돌려준다.

    Args:
        theme: 테마 사전
        action: ACTION_SHUTDOWN / ACTION_RESTART / ACTION_MONITOR_OFF

    Returns:
        accent·accentText·tabFill·onTab·buttonFill·onButton 사전. 모르는 동작이면 종료 값.
    """
    name = _ACTION_TO_NAME.get(action, "shutdown")
    return theme["tokens"]["actions"][name]


def mix(a: str, b: str, ratio: float) -> str:
    """두 색을 섞는다. ratio 는 a 의 비율이다.

    예) mix(accent, surface, 0.16) = "강조색 16% + 카드".

    Args:
        a: '#rrggbb' 색
        b: '#rrggbb' 색
        ratio: 0.0(전부 b) ~ 1.0(전부 a)

    Returns:
        섞은 '#rrggbb' 색
    """
    ratio = min(max(ratio, 0.0), 1.0)
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(x * ratio + y * (1 - ratio)) for x, y in zip(ca, cb)]
    return "#" + "".join(f"{v:02x}" for v in mixed)
