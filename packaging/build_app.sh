#!/bin/bash
# 构建 macOS .app（自用：ad-hoc 签名，不做公证；详见 docs/design-docs/web-ui/spec.md §5）。
#
# 用法：
#   ./packaging/build_app.sh              # 构建 .app
#   ./packaging/build_app.sh --check      # 构建后跑冻结包资源自检
#   ./packaging/build_app.sh --dmg        # 额外产出 dist/wx-obsidian.dmg（自用可选）
#
# 为什么用独立 3.12 构建 venv：开发 venv 是 3.9（已 EOL，且新 PyInstaller 对 3.9
# 支持在退坡）；打包应基于仍在维护的运行时。
set -euo pipefail

cd "$(dirname "$0")/.."

PY_VERSION="${PY_VERSION:-3.12}"
BUILD_VENV="${BUILD_VENV:-.build-venv}"
UV="${UV:-$HOME/.local/bin/uv}"

WANT_DMG=0
for arg in "$@"; do
  case "$arg" in
    --check) ;;  # 资源自检现在总是执行，保留该参数仅为兼容既有调用
    --dmg) WANT_DMG=1 ;;
    -h|--help)
      sed -n '2,9p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数：$arg（可用：--check / --dmg）" >&2
      exit 2
      ;;
  esac
done

# 冻结包不进 TUI，故不装 textual
RUNTIME_DEPS=(
  "fastapi>=0.110"
  "uvicorn>=0.29"
  "requests>=2.31.0"
  "pyyaml>=6.0"
  "apscheduler>=3.10.0"
  "mistune>=3.2.1"
  "jieba>=0.42"
)

APP="dist/wx-obsidian.app"
BIN="${APP}/Contents/MacOS/wx-obsidian"

echo "==> 1/6 准备构建 venv（Python ${PY_VERSION}）"
if [ ! -x "${BUILD_VENV}/bin/python" ]; then
  "${UV}" venv --python "${PY_VERSION}" "${BUILD_VENV}"
fi
"${UV}" pip install --python "${BUILD_VENV}/bin/python" --quiet \
  "pyinstaller>=6.0" "${RUNTIME_DEPS[@]}"

echo "==> 2/6 确认前端产物存在"
if [ ! -f "wx_obsidian/web/static/index.html" ]; then
  echo "缺少 wx_obsidian/web/static/index.html，请先构建前端：" >&2
  echo "  cd wx_obsidian/web/frontend && pnpm install && pnpm build" >&2
  exit 1
fi

echo "==> 3/6 PyInstaller 打包（图标：packaging/AppIcon.icns）"
rm -rf build dist
"${BUILD_VENV}/bin/pyinstaller" --noconfirm --clean packaging/wx-obsidian.spec

echo "==> 4/6 ad-hoc 签名（自用足够；外发需 Developer ID + 公证）"
codesign --force --deep --sign - "${APP}"
codesign --verify --deep --strict "${APP}" && echo "    签名校验通过"

echo "==> 5/6 冻结包资源自检"
"${BIN}" --check-paths

if [ "${WANT_DMG}" = "1" ]; then
  echo "==> 6/6 生成 dmg（自用可选）"
  rm -f dist/wx-obsidian.dmg
  hdiutil create -volname "wx-obsidian" -srcfolder "${APP}" -ov -format UDZO \
    dist/wx-obsidian.dmg >/dev/null
  echo "dmg: $(pwd)/dist/wx-obsidian.dmg"
fi

echo
echo "产物: $(pwd)/${APP}（$(du -sh "${APP}" | cut -f1)）"
echo "安装: cp -R ${APP} /Applications/"
echo "日志: ~/Library/Logs/wx-obsidian/app.log"
echo "冒烟: .venv/bin/python packaging/smoke_frozen.py    # 冻结包端到端自检"
echo
echo "若双击提示「已损坏」（经浏览器下载会被打 quarantine）："
echo "  xattr -dr com.apple.quarantine /Applications/wx-obsidian.app"
