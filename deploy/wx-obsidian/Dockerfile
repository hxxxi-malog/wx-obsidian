# wx-obsidian Web 服务镜像
#
# 多阶段：node 构建前端 → python 运行。前端在构建期编译，因为 static/ 是
# 被 gitignore 的产物；依赖本地 `pnpm build` 会让「镜像可复现」变成
# 「看你这台机器上有没有构建过」。
#
# 容器里没有浏览器，也没有终端：日志落盘 + 页面上的日志面板是唯一的排错通道。

# ---------------------------------------------------------------- 前端构建
FROM node:22-alpine AS frontend

# 这个网络直连 npmjs 不稳（实测 registry.npmjs.org 3.1s，且会 ECONNRESET；
# npmmirror 0.5s），所以默认走镜像。要换：--build-arg NPM_REGISTRY=...
ARG NPM_REGISTRY=https://registry.npmmirror.com
# npm_config_registry 给 pnpm 用；COREPACK_NPM_REGISTRY 给 corepack 下载 pnpm 本体用；
# 关掉下载确认提示，否则非交互构建会卡在提问上
ENV npm_config_registry=${NPM_REGISTRY} \
    COREPACK_NPM_REGISTRY=${NPM_REGISTRY} \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0

RUN corepack enable
WORKDIR /src/wx_obsidian/web/frontend

# 只先拷锁文件：依赖层可以缓存，改源码不必重装依赖
COPY wx_obsidian/web/frontend/package.json wx_obsidian/web/frontend/pnpm-lock.yaml ./
# pnpm 12 默认不允许依赖执行构建脚本，非交互环境（容器/CI）下会直接报错退出。
# esbuild/vue-demi 的 postinstall 只做平台二进制校验/软链（真正的二进制来自
# lockfile 里 @esbuild/linux-* 这类 optionalDependencies），跳过不影响 vite 构建。
# package.json 里的 `pnpm.onlyBuiltDependencies` 在 pnpm 12 已不再生效
# （该配置 10.13+ 移到了 pnpm-workspace.yaml），所以这里用显式开关，不靠它。
RUN pnpm install --frozen-lockfile --config.strict-dep-builds=false

COPY wx_obsidian/web/frontend/ ./
# vite.config.ts 的 outDir 是 ../static，所以产物落在 /src/wx_obsidian/web/static
RUN pnpm build

# ---------------------------------------------------------------- 运行环境
FROM python:3.12-slim AS runtime

# 同上：直连 pypi 也慢/不稳，默认用镜像（与本机 ~/.pip/pip.conf 保持一致）
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HOME=/home/wx \
    TZ=Asia/Shanghai

WORKDIR /app

# 依赖只从 pyproject.toml 取（单一事实来源，不在 Dockerfile 里再抄一份清单）。
# 刻意**不**安装包本体：我们从 /app 的源码树运行，装进 site-packages 的话
# `web/api.py` 的 `Path(__file__).parent / "static"` 会指向那份没有前端产物的副本。
COPY pyproject.toml ./
RUN python -c "import tomllib; \
deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; \
print('\n'.join(deps))" > /tmp/requirements.txt \
    && pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY process_articles.py validate_markdown.py ./
COPY skills/ ./skills/
COPY prompts/ ./prompts/
COPY wx_obsidian/ ./wx_obsidian/
COPY --from=frontend /src/wx_obsidian/web/static ./wx_obsidian/web/static

# 非 root 运行。
# chown 不能省：COPY 会**保留源文件权限**，而本机检出的文件是 0600（git 不记录
# 普通文件的权限位，只记可执行位，所以 clone 出来又是 0644）。不 chown 的话
# 非 root 用户连自己的源码都读不了，容器会以 PermissionError 启动失败。
#
# /home/wx 也必须 chown：上面 `ENV HOME=/home/wx` 在 useradd 之前就生效，而
# `useradd --create-home` 对**已存在**的家目录不改归属。实测探针：useradd 之前
# /home/wx 已经是 root:root（前面某一层以 root 身份碰过 $HOME），于是 wx 用户
# 建不了 ~/.wx-obsidian —— 日志与 web.token 都落不了盘。症状很隐蔽：容器照样
# 起得来、/api/ping 照样 200，只是每次启动都刷「无法写入日志文件」的告警，而
# 容器里唯一的排错通道恰好就是那份日志。
RUN useradd --create-home --uid 1000 wx && chown -R wx:wx /app /home/wx
USER wx

EXPOSE 8765

# 容器内必须绑 wildcard，否则宿主发布出去的端口连不到服务；
# 令牌与来源放行开关由环境变量给（见 .env.example）。
CMD ["python", "-m", "wx_obsidian.web", "--no-open", "--host", "0.0.0.0"]

# /api/ping 免鉴权，正好用来做容器健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/ping', timeout=4).status == 200 else 1)"
