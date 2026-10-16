# GitHub Actions 配置说明

<!-- 文件名刻意不叫 README.md：GitHub 找仓库首页 README 的顺序是
     `.github/README.md` → 根 `README.md` → `docs/README.md`，`.github/` 优先级
     最高。叫 README.md 会把项目首页顶掉（踩过一次：dev 分支首页整个变成这份文档）。
     要改名请避开这三个候选位置。 -->

三个 workflow + 一个依赖更新机器人。**红线**：CI 必须能在干净环境里跑绿，
所以这里不依赖任何「本机手工装过的东西」，也不把本机镜像源写进 workflow。

| 文件 | 触发 | 作用 |
|------|------|------|
| `workflows/ci.yml` | push / PR 到 `main` `dev`，可手动 | 静态检查、双版本单测、前端四道门、端到端冒烟、容器可启动 |
| `workflows/security.yml` | push / PR + 每周一 06:00 + 可手动 | 密钥守卫、gitleaks 全历史、依赖漏洞审计（只报不拦） |
| `workflows/release.yml` | 打 `v*` tag，可手动 | 构建并推送镜像到 GHCR + 建 GitHub Release |
| `scripts/check_secrets.py` | 被 security.yml 调用 | `.env` / 密钥 / `.gitignore` 生效性 / 历史泄漏 |
| `requirements-test.txt` | 被 ci.yml 调用 | pyproject 没声明的测试期依赖（见下） |
| `gitleaks.toml` | 被 security.yml 调用 | gitleaks 白名单（占位模板、测试假值、扫描器自身） |
| `dependabot.yml` | 每周 | 自动更新 action 版本 / Python 依赖 / 前端依赖 |

---

## 一、密钥防泄漏（三道防线）

`.env` 里放着 DeepSeek API Key，`.gitignore` 也写了忽略规则——但「规则存在」
不等于「真的生效」。历史上 `.env` 的精确匹配就盖不住 `.env.bak.*` 备份文件
（备份里同样是密钥），所以这里不靠自觉，靠机器：

**第一道 · `scripts/check_secrets.py`（本仓库自己的守卫，纯标准库）**

| 检查 | 做法 | 为什么 |
|------|------|--------|
| 密钥文件名 | `.env` / `.env.*` / `*.pem` / `*.key` / `id_rsa` / `*.lic` 不允许进索引 | 这类文件本身就是信号，`git add -f` 一次就漏 |
| 文件内容 | `sk-` Key、AWS / GitHub / Slack / Google 令牌、私钥块、JWT、`api_key = "..."` 赋值 | 光看文件名挡不住「把 Key 写进 `config.py`」 |
| `.gitignore` 生效性 | 对 `.env` / `.env.local` / `.env.production` / `.env.bak.*` / `deploy/*/.env` 逐个跑 `git check-ignore --no-index` | 防「规则写错、其实没生效」——本次就是这么发现 `.env.local` / `.env.production` 漏网的 |
| 历史泄漏 | `--history` 扫 `git log -p` 的新增行，报出 commit + 文件 | 密钥进过历史，删文件删不掉，只能换钥匙 |

输出一律脱敏（只露前 4 位 + 长度）。本地也能用，建议加进 pre-commit：

```bash
python .github/scripts/check_secrets.py            # 已跟踪文件 + .gitignore
python .github/scripts/check_secrets.py --staged   # 只查暂存区（提交前）
python .github/scripts/check_secrets.py --history  # 追加查全部历史
```

**第二道 · gitleaks（独立第二意见）**：规则集更全、带熵检测，扫全历史。
用官方 docker 镜像而不是 `gitleaks-action`（后者在组织仓库里要
`GITLEAKS_LICENSE`）。白名单只按路径放行三类内容，不放行任何具体密钥值：
`*.env.example`（占位模板）、`tests/`（掩码功能的假 token）、守卫脚本自身
（里面写着要匹配的正则字面量，必然自命中）。

**第三道 · `.gitignore` 本身**：已改为按前缀忽略并单独放行模板——

```gitignore
.env
.env.*
!.env.example
```

---

## 二、CI 五道门

| Job | 跑什么 | 关键取舍 |
|-----|--------|---------|
| `static-checks` | `ruff check` → `ruff format --check` → `mypy` | mypy **只在 3.9 档跑**：pyproject 目标是 3.9，而 3.12 环境解到 click 8.5（内部用 `match` 语法），mypy 按 3.9 解析必然报语法错——那是环境错配，不是代码问题 |
| `tests` | `pytest tests -q`，矩阵 3.9 + 3.12 | 3.9 是 `requires-python` 下限，3.12 是容器实际运行的那档 |
| `frontend` | `pnpm type-check` → `style-guard` → `test` → `build` | 四道门缺一不可；用 `corepack` 起 pnpm（`package.json` 已钉 `packageManager: pnpm@12.9.1`），与 Dockerfile 同一条路径 |
| `e2e` | `python tests/e2e/smoke_web.py` | 真跑一轮：临时 HOME + 本地 stub 上游，断言笔记落盘、`processed.json` 写入、日志掩码、`POST /api/shutdown` 干净退出。不花 API 费用 |
| `docker` | 构建镜像 + **起容器打 `/api/ping`** | 只 build 不算数：镜像里前端产物缺失、依赖装错都会「构建成功但起不来」。本 job 在本次搭建中真的抓到过一个 `NameError` 启动即崩的版本。它用的是**裸 `docker run`**（不带 compose 的 volume 挂载），所以还顺带守住了「镜像自己在非 root 下能不能落盘」——`/home/wx` 被 root 抢先建出来、导致日志与 token 落不了盘的问题就是这么浮出来的 |

**依赖为什么这么装**（`uv sync --frozen --extra dev --no-install-project`）：

- 依赖只从 `pyproject.toml` / `uv.lock` 取，保证单一事实来源、可复现。
- **不装项目本体**：本仓库是 flat-layout（`deploy/`、`skills/`、`prompts/` 都在
  根目录下），`pip install -e .` 会因 setuptools 自动发现多个顶层包而直接失败。
  lint / 测试都在仓库根目录跑，不装本体不影响 import。
- `requirements-test.txt` 兜底 pyproject 没声明的测试期依赖：`pytest-asyncio`
  （少了 2 个 async 用例直接失败）、`httpx` / `httpx2`（少了 8 个测试收集期就报
  `starlette.testclient` 缺包）、`types-PyYAML` / `types-requests`（少了 mypy
  报 `import-untyped`）。**根治办法是把它们写进 pyproject 的 dev extra 再
  `uv lock`，之后这个文件就能删掉。**

---

## 三、发布（CD）

打 `v*` tag 时：构建镜像 → 推 `ghcr.io/<owner>/wx-obsidian`（semver +
`major.minor` + `sha` + `latest`）→ 建 GitHub Release。

- 只在 tag 上发布，主干推送不推镜像（不把 registry 当 CI 缓存用）。
- 手动触发必须显式 checkout 目标 tag，否则会拿主干代码发一个「v0.1.0」的镜像。
- 建 Release 是幂等的（已存在则跳过）。
- 首次发布后，若要让别人能拉这个包，需在 GitHub 仓库的 Packages 设置里把该
  package 设为 public。

---

## 四、已知边界（有意为之）

1. **依赖漏洞审计只报不拦**：当前 `uv.lock` 解出的 `starlette` / `urllib3` /
   `requests` 就带着待修通告。拿它卡主干只会让人习惯性无视红叉，所以它负责把
   信息摆到台面上，真正拦人的是密钥与代码质量。
2. **浏览器冒烟（`ui-smoke.mjs`）没有进 CI**：它需要系统 Chrome，且要有已抓取
   的文章数据（本次实测空状态下 49/50 通过，失败项是「点击行打开详情抽屉」）。
   直接接进 CI 会长期红。要接的话得先给它造种子数据。
3. **`pip install -e .` 在本仓库不可用**（flat-layout + 缺 `[build-system]`），
   而 README 的源码安装章节写的是这条命令。CI 绕开了它，但文档与实现不一致，
   建议二选一：给 `pyproject.toml` 补 `[tool.setuptools] packages` 配置，
   或把 README 改成 `uv sync`。
4. **5 个「在 Obsidian 中打开」的用例在 Linux 上会 skip**：`open_in_obsidian()`
   走 macOS 的 `open obsidian://...`，非 macOS 直接抛 `RuntimeError("仅支持 macOS")`，
   所以 `tests/test_web/test_articles.py` 里这 5 个用例标了
   `@macos_only = pytest.mark.skipif(sys.platform != "darwin")`。它们在 macOS
   （开发机）上照常跑，在 Linux CI 上报 skip。
   **代价要说清**：这组逻辑（注册表校验、URI 百分号编码）在 Linux CI 上不再被覆盖。
   想要覆盖的话有两条路：补一个 macOS runner 的 job，或把那 5 个用例里的平台判定
   改成可注入的开关（它们本来就把 `subprocess.run` mock 掉了，纯粹被平台守卫挡住）。
5. **runner 固定 `ubuntu-24.04`，不用 `ubuntu-latest`**：`ubuntu-latest` 自
   2026-10-19 起迁往 Ubuntu 26，而 `actions/python-versions` 目前**没有 3.9 的
   26.04 构建**（只有 22.04 / 24.04），继续跟 latest 会让 3.9 那档突然找不到解释器。
   固定版本后「哪天开始红」也可归因。

## 五、本地复现 CI

```bash
# Python 三道门
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy wx_obsidian/ process_articles.py validate_markdown.py
.venv/bin/python -m pytest tests -q

# 端到端 + 密钥守卫
.venv/bin/python tests/e2e/smoke_web.py
python .github/scripts/check_secrets.py --history

# 前端四道门
cd wx_obsidian/web/frontend && pnpm type-check && pnpm style-guard && pnpm test && pnpm build
```
