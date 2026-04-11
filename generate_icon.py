"""
generate_icon.py - 빌드 시 앱 아이콘(.ico) 생성 스크립트.

트레이 아이콘과 동일한 전원 버튼 심볼을 Pillow로 그려
multi-size ICO 파일(app_icon.ico)을 생성한다.
build.bat 에서 PyInstaller 실행 직전에 호출된다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from cfg.config import TRAY_ICON_COLOR_IDLE


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """HEX 색상을 RGB 튜플로 변환."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def create_power_icon(size: int, color: str) -> Image.Image:
    """전원 버튼 심볼 아이콘 생성.

    shutdown_scheduler.py의 _create_tray_icon_image 와 동일한 디자인.

    Args:
        size: 정사각형 한 변 픽셀
        color: 배경 원 '#RRGGBB' 색상

    Returns:
        RGBA PIL Image
    """
    w = h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r, g, b = _hex_to_rgb(color)

    margin = max(2, size // 16)
    draw.ellipse(
        [margin, margin, w - margin, h - margin],
        fill=(r, g, b, 255),
    )

    symbol_color = (255, 255, 255, 255)
    inner_margin = margin + max(4, size // 6)
    sx0, sy0 = inner_margin, inner_margin
    sx1, sy1 = w - inner_margin, h - inner_margin
    arc_width = max(2, size // 12)

    draw.arc(
        [sx0, sy0, sx1, sy1],
        start=300,
        end=240,
        fill=symbol_color,
        width=arc_width,
    )

    cx = w // 2
    line_top = sy0 - max(1, size // 24)
    line_bottom = (sy0 + sy1) // 2 - max(1, size // 16)
    draw.line(
        [(cx, line_top), (cx, line_bottom)],
        fill=symbol_color,
        width=arc_width,
    )
    return img


def main() -> None:
    """multi-size .ico 파일 생성."""
    sizes = [16, 24, 32, 48, 64, 128, 256]
    # 가장 큰 사이즈를 기준으로 생성하고 ICO가 자동으로 여러 사이즈 포함
    base = create_power_icon(256, TRAY_ICON_COLOR_IDLE)
    out_path = Path(__file__).parent / "app_icon.ico"
    base.save(
        out_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"[generate_icon] wrote {out_path}")


if __name__ == "__main__":
    main()
