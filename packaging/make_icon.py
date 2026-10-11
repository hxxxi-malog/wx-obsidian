#!/usr/bin/env python3
"""生成 `packaging/AppIcon.icns`（`.app` 的图标占位）。

自用项目的图标不需要设计稿，但**不能没有**：没有图标的 .app 在 Dock 与
Finder 里是一张白纸，和「系统出错了」长得一模一样。这里用纯几何图形画一个
（不依赖字体，避免不同机器渲染不一致），风格与前端主色一致的蓝紫渐变。

流程：Pillow 画 1024×1024（4 倍超采样后缩放做抗锯齿）→ `sips` 生成各尺寸 →
`iconutil` 打包成 .icns。两个工具都是 macOS 自带。

用法::

    python packaging/make_icon.py          # 重新生成 packaging/AppIcon.icns
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
SUPERSAMPLE = 4
ICON_NAME = "AppIcon.icns"
# iconutil 要求 iconset 里文件名严格按尺寸命名
ICONSET_SIZES = (16, 32, 64, 128, 256, 512, 1024)

TOP_COLOR = (74, 108, 247)
BOTTOM_COLOR = (126, 87, 194)
CARD_COLOR = (255, 255, 255)
LINE_COLOR = (203, 213, 235)
NODE_COLOR = (74, 222, 168)


def _gradient(size: int) -> Image.Image:
    """竖向渐变背景。"""
    image = Image.new("RGB", (1, size))
    for y in range(size):
        ratio = y / max(1, size - 1)
        image.putpixel(
            (0, y),
            tuple(
                round(TOP_COLOR[channel] + (BOTTOM_COLOR[channel] - TOP_COLOR[channel]) * ratio)
                for channel in range(3)
            ),
        )
    return image.resize((size, size), Image.Resampling.NEAREST)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def render(size: int = SIZE) -> Image.Image:
    """画图标：圆角渐变底 + 白色卡片 + 三条文本线 + 一个「知识节点」圆点。"""
    canvas = size * SUPERSAMPLE
    image = _gradient(canvas).convert("RGBA")
    image.putalpha(_rounded_mask(canvas, radius=int(canvas * 0.22)))

    draw = ImageDraw.Draw(image)
    # 卡片
    card_left, card_top = int(canvas * 0.22), int(canvas * 0.20)
    card_right, card_bottom = int(canvas * 0.78), int(canvas * 0.78)
    draw.rounded_rectangle(
        (card_left, card_top, card_right, card_bottom),
        radius=int(canvas * 0.055),
        fill=CARD_COLOR,
    )

    # 三条文本线（最后一条短一些，像段落）
    line_height = int(canvas * 0.035)
    gap = int(canvas * 0.075)
    top = card_top + int(canvas * 0.09)
    widths = (0.40, 0.36, 0.22)
    for index, ratio in enumerate(widths):
        y = top + index * gap
        draw.rounded_rectangle(
            (card_left + int(canvas * 0.07), y, card_left + int(canvas * ratio), y + line_height),
            radius=line_height // 2,
            fill=LINE_COLOR,
        )

    # 右下角节点：呼应「双向链接 / 知识图谱」
    node_radius = int(canvas * 0.095)
    center_x, center_y = int(canvas * 0.705), int(canvas * 0.705)
    draw.ellipse(
        (
            center_x - node_radius,
            center_y - node_radius,
            center_x + node_radius,
            center_y + node_radius,
        ),
        fill=NODE_COLOR,
        outline=CARD_COLOR,
        width=int(canvas * 0.018),
    )

    return image.resize((size, size), Image.Resampling.LANCZOS)


def build_icns(target: Path) -> None:
    """生成多尺寸 iconset 并交给 iconutil 打包。"""
    if shutil.which("iconutil") is None or shutil.which("sips") is None:
        raise SystemExit("需要 macOS 自带的 iconutil 与 sips")

    master = render()
    with tempfile.TemporaryDirectory(prefix="wx-icon-") as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        master.save(iconset / "icon_512x512@2x.png")
        for size in ICONSET_SIZES:
            if size == 1024:
                continue
            resized = master.resize((size, size), Image.Resampling.LANCZOS)
            resized.save(iconset / f"icon_{size}x{size}.png")
            if size <= 512:
                resized.save(iconset / f"icon_{size // 2}x{size // 2}@2x.png")

        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 .app 图标")
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent / ICON_NAME),
        help="输出 .icns 路径",
    )
    parser.add_argument("--png", default="", help="同时导出一张 512px PNG（预览用）")
    args = parser.parse_args()

    target = Path(args.output)
    build_icns(target)
    print(f"已生成 {target}（{target.stat().st_size // 1024} KB）")

    if args.png:
        preview = Path(args.png)
        render(512).save(preview)
        print(f"已导出预览 {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
