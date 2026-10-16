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
| `workflows/security.yml` | push / PR + 每周一 06:00 + 可手动 | 密钥守卫、gitleaks 全历史、依赖漏洞审计（3.9 + 3.12 双档，只报不拦） |
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
- 手动触发（workflow_dispatch）有**两处**必须显式处理，少一处就会发错：
  1. `checkout` 的 `ref` 要指向目标 tag，否则拿主干代码去发一个「v1.0.0」的镜像；
  2. 标签推导：`type=semver` 是从 `github.ref` 取版本的，而 dispatch 时它是**分支名**，
     解析不出 tag 会静默跳过 —— 日志里表现为 `type=semver,...,value=,match=`，
     结果是只推 `latest` + `sha`、版本标签原地不动（v1.0.0 重发时踩过）。
     解法：先解析出 `${{ inputs.tag || github.ref_name }}`，再用 `value=` 喂给
     两条 semver 规则，tag 推送与手动触发共用同一套推导。
- 建 Release 是幂等的（已存在则跳过）；重发同一个 tag 会覆盖镜像标签，
  所以补构建配置不需要发新版本号。
- **多架构（amd64 + arm64）**：CI 里那个容器 job 只验本机架构（快），跨架构那半
  只在发布时构建 —— 需要 `docker/setup-qemu-action`（必须先于 buildx，buildx 靠
  它注册 binfmt）+ 给 build-push 传 `platforms: linux/amd64,linux/arm64`。
  缺 arm64 时 Apple Silicon 上 `docker pull` 会直接报
  `no matching manifest for linux/arm64/v8`（v1.0.0 首发踩过这个坑）。
  多架构镜像不能配 `load: true`（装不进本地 docker），只能 push。
  代价：arm64 那半走 QEMU 模拟，前端构建明显变慢。
- 包可见性：**仓库是 public 时 GHCR 包默认也是 public**，匿名即可拉取
  （实测 v1.0.0 不登录就能解析 manifest）；只有仓库转 private 时才需要去
  Packages 设置里调整。

---

## 四、已知边界（有意为之）

1. **依赖漏洞审计审两个 Python 档，只报不拦**：`uv.lock` 是**按 Python 分档
   解析**的，同一个包在不同档可以是不同版本（`starlette`：3.9 档 0.49.3、
   3.12 档 1.8.0）。只审 3.9 那一档会两头落空 —— 报出来的是容器里根本不发货的
   版本，而容器真正跑的 3.12 档反而从没被审过。所以现在是 3.9 + 3.12 矩阵。
   结果写进 job summary + warning 注解，**job 自己保持绿色**，只有 pip-audit
   执行失败才红：红叉恒等于「真出事了」，而不是「上游又发通告了」。
   两个实现细节：pip-audit 用 `uv tool run` 跑在隔离环境里（装进被审环境的话，
   它会把自己的依赖 pip / msgpack / filelock 也审一遍，报出一堆与项目无关的
   通告），`--path` 指向被审环境的 `site-packages`；同一条通告 pip-audit 会按
   不同来源列两次，summary 里按 (包, 版本, 通告 ID) 去重。
   修完后的实测现状：**3.12 档 0 条**（容器里跑的那套已干净），**3.9 档 13 条**
   （anyio / requests / starlette / urllib3）。
   3.9 档这 13 条是**结构性**的：它们的修复版本都要求 `>=3.10`（`urllib3 2.8.0`、
   `starlette 1.x` 皆然），2.6.3 是最后一个支持 3.9 的 urllib3 —— 只要还宣称
   `requires-python >=3.9`，这一档就修不掉。要么接受它长期挂在 summary 里，
   要么放弃 3.9（同步改 `requires-python` 与静态检查那档）。
   本次据此修掉的真问题：`mistune 3.2.1 → 3.3.4`（10 条；直接依赖、所有 Python
   共用，容器也发货）与 `urllib3 2.7.0 → 2.8.0`（3 条；3.10+ 档，即容器那档）。
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
6. **`mypy` 的大版本被 Dependabot 忽略**：`pyproject.toml` 钉 `mypy>=1.10,<2`。
   mypy 2.x 起 `requires-python >=3.10`（PyPI 实测）且移除了 3.9 目标，会让
   `[tool.mypy] python_version = "3.9"` 形同虚设；而静态检查 job 只在 3.9 档跑，
   放开上界后 CI 不会红 —— 属于「绿灯 + 保护静默消失」，所以在
   `dependabot.yml` 里显式 ignore `mypy >= 2`。
   同类注意：只放宽约束、不动 `uv.lock` 的 PR（Dependabot 的 range 更新就是这样）
   会以全绿通过，因为锁文件里还是旧版本 —— 它改的是「以后会解到什么」。

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
