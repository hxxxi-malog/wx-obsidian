#!/bin/bash
# 手动跑一轮：抓取 → Vision → LLM → 写笔记。
#
# 为什么没有系统级定时任务：原来的 macOS LaunchAgent（每日 9:03）在 macOS 15 的
# TCC 下无法读取 ~/Downloads 里的脚本与 vault —— 实测 launchd.err 41 次全部是
# "Operation not permitted"，且只把仓库搬出 ~/Downloads 也没用（vault 也在里面）。
# 需要自动化时请把仓库移出 ~/Downloads，再用系统调度器（cron / launchd /
# systemd timer）自行触发 —— 本项目不内置调度器。
#
# 用法：
#   ./run.sh                # 处理所有未处理文章
#   ./run.sh --limit 3      # 只处理 3 篇
#   ./run.sh --force        # 强制重新处理
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs

export PYTHONUNBUFFERED=1
# 同时打到终端并追加进日志，方便手动观察进度
.venv/bin/python process_articles.py "$@" 2>&1 | tee -a logs/run.log
exit "${PIPESTATUS[0]}"
