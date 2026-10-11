#!/bin/bash
# 构建 macOS .app（自用：ad-hoc 签名，不做公证；详见 docs/design-docs/web-ui/spec.md §5）。
#
# 用法：
#   ./packaging/build_app.sh              # 构建 .app
#   ./packaging/build_app.sh --check      # 构建后跑冻结包资源自检
#
# 为什么用独立 3.12 构建 venv：开发 venv 是 3.9（已 EOL），
# 而 PyInstaller 打包应基于仍在维护的运行时。
set -euo pipefail

cd "$(dirname "$0")/.."

PY_VERSION="${PY_VERSION:-3.12}"
BUILD_VENV="${BUILD_VENV:-.build-venv}"
UV="${UV:-$HOME/.local/bin/uv}"

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

echo "==> 1/5 准备构建 venv（Python ${PY_VERSION}）"
if [ ! -x "${BUILD_VENV}/bin/python" ]; then
  "${UV}" venv --python "${PY_VERSION}" "${BUILD_VENV}"
fi
"${UV}" pip install --python "${BUILD_VENV}/bin/python" --quiet \
  "pyinstaller>=6.0" "${RUNTIME_DEPS[@]}"

echo "==> 2/5 确认前端产物存在"
if [ ! -f "wx_obsidian/web/static/index.html" ]; then
  echo "缺少 wx_obsidian/web/static/index.html，请先构建前端：" >&2
  echo "  cd wx_obsidian/web/frontend && pnpm install && pnpm build" >&2
  exit 1
fi

echo "==> 3/5 PyInstaller 打包"
rm -rf build dist
"${BUILD_VENV}/bin/pyinstaller" --noconfirm --clean packaging/wx-obsidian.spec

echo "==> 4/5 ad-hoc 签名（自用足够；外发需 Developer ID + 公证）"
codesign --force --deep --sign - dist/wx-obsidian.app

echo "==> 5/5 完成"
APP="dist/wx-obsidian.app"
echo "产物: $(pwd)/${APP}"
echo "安装: cp -R ${APP} /Applications/"

if [ "${1:-}" = "--check" ]; then
  echo "==> 冻结包资源自检"
  "${APP}/Contents/MacOS/wx-obsidian" --check-paths
fi
