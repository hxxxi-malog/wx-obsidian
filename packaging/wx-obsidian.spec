# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：wx-obsidian Web 模式 macOS .app。

关键点（背景见 docs/design-docs/web-ui/spec.md §5.2）：

- **onedir 而非 onefile**：onefile 每次启动都会解压到临时目录，冷启动十几秒。
- **skills/ prompts/**：`wx_obsidian/config.py` 用 `Path(__file__).parent.parent`
  定位资源，冻结后该值正是 `sys._MEIPASS`，所以这两份数据必须放到归档根。
- **wx_obsidian/web/static**：同上，必须落在 `_MEIPASS/wx_obsidian/web/static`，
  才能被 `web/api.py` 的 `Path(__file__).parent / "static"` 命中。
- **jieba**：词典是包内数据文件，静态分析抓不到，需 collect_data_files。
- **uvicorn**：大量动态 import（协议/lifespan/loop 实现），必须显式 hiddenimports。
- **排除 textual / wx_obsidian.tui**：TUI 保留在源码里，但不进 .app。
"""

import os
import tomllib

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH 由 PyInstaller 注入)

STATIC_DIR = os.path.join(ROOT, "wx_obsidian", "web", "static")
if not os.path.isfile(os.path.join(STATIC_DIR, "index.html")):
    raise SystemExit(
        "缺少 wx_obsidian/web/static/index.html：先构建前端\n"
        "  cd wx_obsidian/web/frontend && pnpm install && pnpm build"
    )

# 版本只在 pyproject.toml 里写一份，避免 .app 与实际代码版本漂移
with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
    VERSION = tomllib.load(fh)["project"]["version"]

ICON = os.path.join(ROOT, "packaging", "AppIcon.icns")
if not os.path.isfile(ICON):
    ICON = None  # 没图标也能构建，只是 Dock 里是一张白纸

datas = [
    (os.path.join(ROOT, "skills"), "skills"),
    (os.path.join(ROOT, "prompts"), "prompts"),
    (STATIC_DIR, "wx_obsidian/web/static"),
]
datas += collect_data_files("jieba")

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "wx_obsidian.web",
    "wx_obsidian.web.api",
    "wx_obsidian.web.runtime",
]

excludes = [
    "textual",
    "wx_obsidian.tui",
    "tkinter",
    "pytest",
    "mypy",
    "ruff",
]

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "packaging", "entry_web.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="wx-obsidian",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # .app 形态；排错靠 ~/Library/Logs（T-16）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="wx-obsidian",
)

app = BUNDLE(  # noqa: F821
    coll,
    name="wx-obsidian.app",
    icon=ICON,
    bundle_identifier="com.malog.wx-obsidian",
    info_plist={
        "CFBundleName": "wx-obsidian",
        "CFBundleDisplayName": "wx-obsidian",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        # vault 常在 ~/Documents 或 ~/Downloads 下；声明用途后系统授权弹窗
        # 才会说明白「这个 app 为什么想读这些目录」，而不是一句无意义的询问。
        "NSDocumentsFolderUsageDescription": "读取你指定的 Obsidian 知识库目录，用于写入生成的笔记。",
        "NSDesktopFolderUsageDescription": "读取你指定的 Obsidian 知识库目录，用于写入生成的笔记。",
        "NSDownloadsFolderUsageDescription": "读取你指定的 Obsidian 知识库目录，用于写入生成的笔记。",
    },
)
