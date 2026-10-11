# we-mp-rss 数据源部署

wx-obsidian 的文章数据源。[we-mp-rss](https://github.com/rachelos/we-mp-rss) 取代了已归档的
WeWe RSS（其闭源中转已下线）。本目录是**唯一权威的部署配方**，全部纳入版本管理。

> 为什么用派生镜像而不是直接用上游 `ghcr.io/rachelos/we-mp-rss:latest`：
> 上游把文章的发布时间写成了抓取时刻（`_collect_latest_via_cover()` 里硬编码
> `int(time.time())`）。这会让 wx-obsidian 侧 `fetch.max_days` 的日期过滤失效、
> 笔记 frontmatter 的 `date` 变成抓取日。`patch/` 下的补丁把真实发布时间
> （从同一次请求的 `/web/mp/content` 页面里已有的 `var ct`）回填回去。
> 详见 `Dockerfile` 顶部注释。

## 首次部署

```bash
cd deploy/we-mp-rss
cp .env.example .env          # 填入 WERSS_PASS 等
docker compose up -d --build
```

启动后访问 <http://localhost:8001> 完成初始化，并登录微信读书（`/weread` 扫码，
或把完整 Cookie 填进 `.env` 的 `WEREAD_COOKIE`）。

## 关键配置

| 位置 | 项 | 值 | 原因 |
|------|----|----|------|
| `config.yaml` | `gather.model` | `weread_mp` | 默认的 `web` 模式走公众平台后台接口（`cgi-bin/appmsgpublish`），极易 `ret=200013`，且上游遇该错误会提前 `break`（issue #469） |
| `config.yaml` | `gather.content` | `True` | 抓取正文全文 |
| compose env | `RSS_FULL_CONTEXT` | `True` | JSON Feed 的 `content:encoded` 带全文 |
| compose env | `TOKEN_EXPIRE_MINUTES` | `525600`（1 年） | 管理 API 令牌有效期。不写进 compose 的 `environment` 就等于没设——本 compose 是显式列表，没有 `env_file`，`.env` 里的值不会自动进容器 |
| compose ports | `127.0.0.1:8001:8001` | 仅回环 | 管理 API 是全权限的，不暴露到局域网 |

> ⚠️ `GATHER.MODEL` / `GATHER.CONTENT` 这类**带点的环境变量不要**写在 compose 里：
> 实测 `docker inspect` 能看到，但容器内 `os.getenv()` 取不到，生效值仍是
> `config.yaml` 的默认 `model='web'`。必须直接写进挂载的 `config.yaml`。

## 管理 API 令牌（wx-obsidian 的账号/公众号页要用）

`/feed/*.json` 是公开只读接口，**抓取不需要凭据**。但「账号管理」「公众号管理」
两页读的是 `/api/v1/wx/*`，要 Bearer 令牌，否则一律 401，界面只能显示「查不到」。

```bash
# 用 .env 里的 WERSS_USER / WERSS_PASS 换一个令牌（有效期见 TOKEN_EXPIRE_MINUTES）
set -a; . deploy/we-mp-rss/.env; set +a
curl -s -X POST http://localhost:8001/api/v1/wx/auth/token \
  -d "username=$WERSS_USER&password=$WERSS_PASS" | python3 -c \
  "import json,sys;print(json.load(sys.stdin)['access_token'])"
```

把输出粘到 wx-obsidian 的「配置管理 → 文章数据源 → 访问令牌」。令牌是 JWT，
Web 界面会解析 `exp` 显示到期时间，过期后账号页会显式提示而不是静默变成「未登录」。

## 安全约定（务必遵守）

- `.env`（含 `WERSS_PASS` / `WEREAD_COOKIE`）和 `data/`（SQLite + 订阅/正文数据）
  **不入版本库**，已在根 `.gitignore` 中排除。
- `config.yaml` 里所有凭据都是 `${VAR}` 引用，本身不含密钥，因此可以入库。
- 部署只绑定 `127.0.0.1`；如需跨机器访问请自行加反向代理与鉴权。

## 从旧的仓库外目录迁过来

如果之前用的是仓库外的 `we-mp-rss-deploy/`（数据在它自己的 `data/`、凭据在它的 `.env`）：

```bash
cd deploy/we-mp-rss
cp ../../../we-mp-rss-deploy/.env .            # 凭据
cp -R ../../../we-mp-rss-deploy/data .         # 已有订阅与文章（SQLite）
docker compose up -d --build
```

## 验证

```bash
# 只读接口探活（不触发采集）
curl -s "http://localhost:8001/feed/all.json?limit=1" | head -c 200

# 镜像内没有 curl，healthcheck 用 wget
docker inspect --format '{{.State.Health.Status}}' we-mp-rss
```

## 维护提示（升级上游镜像时必读）

`patch/weread_mp.py` 是从 `ghcr.io/rachelos/we-mp-rss:latest` 容器里拷出的**整文件副本**，
只改了发布时间回填那一处。升级上游后必须重新导出并重新打补丁，否则会用旧文件
覆盖掉上游的新修复（例如上游对短链 token 中 `~` 的处理变更）：

```bash
docker cp we-mp-rss:/app/core/wx/model/weread_mp.py patch/weread_mp.py
# 重新应用补丁，然后 diff 确认唯一差异就是发布时间回填
docker compose up -d --build
```
