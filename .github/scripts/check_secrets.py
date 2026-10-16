#!/usr/bin/env python3
"""密钥防泄漏守卫（CI 与本地提交前共用）。

为什么要有它：这个仓库的 `.env` 里放着 DeepSeek API Key，`.gitignore` 里也写了
忽略规则——但「规则存在」不等于「真的生效」，更不等于「以后不会被改掉」。
历史上 `.env` 的精确匹配就盖不住 `.env.bak.*` 备份文件（备份里同样是密钥），
所以这里把三件事固化成脚本，让机器来判：

1. **文件名**：`.env` / `.env.*` / `*.pem` / `*.key` / `id_rsa` / `*.lic` 等
   天生装密钥的文件，不允许出现在索引里（`*.example` 这类纯占位模板放行）。
2. **文件内容**：已跟踪文件里不允许出现真密钥——`sk-` 开头的 Key、AWS /
   GitHub / Slack / Google 令牌、私钥块、JWT，以及 `api_key = "..."` 这类赋值。
   占位值（`your-...`、`${VAR}`、`<...>`）与 `tests/` 下的假值走白名单。
3. **`.gitignore` 真的生效**：用 `git check-ignore` 验证 `.env` 及其常见变体
   确实被判为忽略（而不是「写了规则但写错、其实没生效」）。

`--history` 追加扫描 `git log -p` 的新增行，用来发现**已经进过历史**的泄漏
（历史里的密钥删文件是删不掉的，只能换钥匙）。它与 gitleaks 是双保险：
gitleaks 规则更全，本脚本则覆盖 `.env` 这类「文件名本身就是信号」的场景。

用法::

    python .github/scripts/check_secrets.py             # 扫已跟踪文件 + .gitignore
    python .github/scripts/check_secrets.py --staged    # 只扫暂存区（pre-commit 用）
    python .github/scripts/check_secrets.py --history   # 追加扫全部历史

退出码：0 = 干净；1 = 有发现；2 = 环境不满足（不是 git 仓库 / git 不可用）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------

# 1) 文件名：这些文件天生装密钥，不该进索引（`.env.example` 这类模板除外）
SECRET_FILE_RE = re.compile(
    r"""(?ix)
    (^|/)(
        \.env(\..+)?                       # .env / .env.local / .env.production
      | \.npmrc | \.netrc | \.pypirc       # 包管理器凭据
      | credentials\.json
      | id_(rsa|dsa|ecdsa|ed25519)         # SSH 私钥（.pub 是公钥，放行）
      | .+\.(pem|key|p12|pfx|jks|keystore|ppk)
      | .+\.lic                            # 授权文件（如 we-mp-rss 的 wx.lic）
    )$
    """
)
# 纯占位 / 模板文件放行：仓库靠它们告诉用户该配哪些变量，本身不含真值
PLACEHOLDER_SUFFIXES = (".example", ".sample", ".template", ".dist")

# 2) 内容：命中即报（除非落在白名单里）
CONTENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("deepseek/openai key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
]
# 赋值型：`api_key = "..."` / `token: xxx`。值要求 16 位以上，避免误伤
# `token: str = ""` 这类类型标注。
GENERIC_ASSIGN_RE = re.compile(
    r"""(?ix)
    \b(api[_-]?key|apikey|access[_-]?key|secret[_-]?key|client[_-]?secret
      |password|passwd|token|auth[_-]?token)\b
    \s*[:=]\s*
    ["']?([A-Za-z0-9+/=_-]{16,})["']?
    """
)
# 明显是占位符的值（配合上面的赋值规则用）
PLACEHOLDER_VALUE_RE = re.compile(
    r"""(?ix)^(
        your | my | the | test | fake | dummy | example | sample | placeholder
      | changeme | redacted | none | null | todo | xxx+ | \$\{ | \$[A-Z_]+ | <
    )"""
)
# 纯字母的值按「类型名 / 标识符」处理，不当作密钥。
# 依据：真实密钥几乎必然含数字或符号（`sk-xxx`、`AKIA...`、base64 里的 `+/=`），
# 而代码里的类型标注恰恰是纯字母——`token: SourceTokenState` 就是这么被误报的。
# 代价是「32 位纯字母」这种密钥会漏，但它由上面的厂商规则与 gitleaks 的
# 熵检测兜底，不值得为它保留一个天天误报的规则。
IDENTIFIER_VALUE_RE = re.compile(r"^[A-Za-z]+$")

# 3) 白名单：按路径放行，并且必须写清理由
ALLOWLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\.github/scripts/check_secrets\.py$"), "守卫自身的规则字面量"),
    (re.compile(r"^\.github/gitleaks\.toml$"), "扫描器配置"),
    (re.compile(r"^tests/"), "测试里的假密钥 / 假 token（fixture）"),
    (re.compile(r"(^|/)\.env\.(example|sample|template)$"), "纯占位模板"),
]

# 4) .gitignore 必须真的忽略这些路径（用 git check-ignore 行为验证，而不是字符串匹配）
IGNORE_PROBES = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.bak.20260101000000",
    "deploy/wx-obsidian/.env",
    "deploy/we-mp-rss/.env",
)


class Finding:
    """一条发现。location 形如 `path:line` 或 `path`。"""

    def __init__(self, rule: str, location: str, detail: str) -> None:
        self.rule = rule
        self.location = location
        self.detail = detail

    def render(self) -> str:
        return f"  ✗ [{self.rule}] {self.location}\n      {self.detail}"


# ---------------------------------------------------------------------------
# git 辅助
# ---------------------------------------------------------------------------


def _git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )


def _split_z(output: str) -> list[str]:
    return [item for item in output.split("\0") if item]


def tracked_files() -> list[str]:
    """索引里的文件（= 会被提交、会被 push 上去的东西）。"""
    return _split_z(_git(["ls-files", "-z"]).stdout)


def staged_files() -> list[str]:
    """暂存区相对 HEAD 的新增/修改文件（pre-commit 场景）。"""
    result = _git(["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"])
    return _split_z(result.stdout)


def _decode(data: bytes) -> str | None:
    if b"\0" in data:  # 二进制文件不参与文本扫描
        return None
    return data.decode("utf-8", errors="replace")


def read_worktree(path: str) -> str | None:
    full = REPO_ROOT / path
    if not full.is_file():
        return None
    return _decode(full.read_bytes())


def read_staged(path: str) -> str | None:
    result = _git(["show", f":{path}"])
    if result.returncode != 0:
        return None
    return result.stdout


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------


def is_allowlisted(path: str) -> bool:
    return any(pattern.search(path) for pattern, _ in ALLOWLIST)


def redact(value: str) -> str:
    """日志里不留明文：只露前 4 位 + 长度。"""
    head = value[:4]
    return f"{head}…（{len(value)} 字符，已脱敏）"


def check_file_names(paths: Sequence[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not SECRET_FILE_RE.search(path):
            continue
        if path.endswith(PLACEHOLDER_SUFFIXES):
            continue
        findings.append(
            Finding(
                "密钥文件名",
                path,
                "这个文件不该入库。若确实是模板，请命名为 *.example / *.sample；"
                "若已经提交过，除了删文件还要去服务端换掉这把钥匙。",
            )
        )
    return findings


def check_contents(items: Sequence[tuple[str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    for path, text in items:
        if is_allowlisted(path):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            location = f"{path}:{line_no}"
            for rule_name, pattern in CONTENT_RULES:
                match = pattern.search(line)
                if match:
                    findings.append(
                        Finding(rule_name, location, f"疑似明文密钥：{redact(match.group(0))}")
                    )
            generic = GENERIC_ASSIGN_RE.search(line)
            if generic is None:
                continue
            value = generic.group(2)
            if PLACEHOLDER_VALUE_RE.match(value) or IDENTIFIER_VALUE_RE.match(value):
                continue
            findings.append(
                Finding("疑似密钥赋值", location, f"{generic.group(1)} = {redact(value)}")
            )
    return findings


def check_gitignore() -> list[Finding]:
    findings: list[Finding] = []
    for probe in IGNORE_PROBES:
        # --no-index：即使文件已被 git add 过，也按规则判断（正是要查这种情况）
        result = _git(["check-ignore", "-q", "--no-index", "--", probe])
        if result.returncode != 0:
            findings.append(
                Finding(
                    "gitignore 失效",
                    probe,
                    "该路径没有被 .gitignore 忽略。请补规则（形如 `.env.*`），"
                    "否则一次 `git add -A` 就会把密钥提交上去。",
                )
            )
    return findings


def scan_history() -> list[Finding]:
    """扫 git log -p 的新增行：找已经进过历史的泄漏。

    只报「新增行」（`+` 开头）——删除行说明当时就没提交，不算泄漏。
    """
    result = _git(["log", "-p", "--all", "--no-merges", "--format=@@COMMIT %H"])
    if result.returncode != 0:
        return []

    findings: list[Finding] = []
    commit = "unknown"
    path = "unknown"
    for line in result.stdout.splitlines():
        if line.startswith("@@COMMIT "):
            commit = line[len("@@COMMIT ") :][:12]
            continue
        if line.startswith("+++ b/"):
            path = line[len("+++ b/") :]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        added = line[1:]
        if is_allowlisted(path):
            continue
        for rule_name, pattern in CONTENT_RULES:
            match = pattern.search(added)
            if match:
                findings.append(
                    Finding(
                        f"历史泄漏/{rule_name}",
                        f"{commit}:{path}",
                        f"疑似明文密钥：{redact(match.group(0))}"
                        "（历史里的东西删文件删不掉，需要去服务端吊销并换新）",
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="密钥防泄漏守卫")
    parser.add_argument("--staged", action="store_true", help="只扫暂存区（pre-commit）")
    parser.add_argument("--history", action="store_true", help="追加扫描全部 git 历史")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if _git(["rev-parse", "--git-dir"]).returncode != 0:
        print("错误：当前目录不是 git 仓库，无法判断哪些文件会被提交。", file=sys.stderr)
        return 2

    paths = staged_files() if args.staged else tracked_files()
    scope = "暂存区" if args.staged else "已跟踪文件"
    print(f"扫描范围：{scope}（{len(paths)} 个文件）")

    findings = check_file_names(paths)

    contents: list[tuple[str, str]] = []
    for path in paths:
        text = read_staged(path) if args.staged else read_worktree(path)
        if text is not None:
            contents.append((path, text))
    findings += check_contents(contents)

    if not args.staged:
        findings += check_gitignore()

    if args.history:
        print("扫描范围：git 全部历史的新增行")
        findings += scan_history()

    if findings:
        print(f"\n发现 {len(findings)} 处问题：\n")
        for finding in findings:
            print(finding.render())
        print(
            "\n处理建议：\n"
            "  1. 从索引里移除：git rm --cached <文件>（保留本地文件）\n"
            "  2. 确认 .gitignore 覆盖该路径：git check-ignore -v <文件>\n"
            "  3. 若密钥已经进过历史或已 push：立刻到服务端吊销并换新，"
            "改历史（filter-repo）只是补救。\n"
        )
        return 1

    print("✓ 未发现密钥泄漏：文件名、已跟踪内容、.gitignore 生效性都正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
