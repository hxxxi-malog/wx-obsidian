#!/usr/bin/env node
/**
 * 风格守卫：把「说好的设计约束」变成会红灯的检查。
 *
 * 为什么需要它：全量重构最容易在细节上漂回去——某个页面偷用 Element Plus、
 * 某个组件写死 #409eff、某处又出现 emoji 图标。人工 review 会漏，机器不会。
 *
 * 用法：node scripts/style-guard.mjs
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(FRONTEND_ROOT, 'src')

/** 允许出现裸色值/字体名的文件（token 定义处） */
const TOKEN_FILES = new Set(['src/styles/tokens.css'])

/** 允许出现 /api/ 字面量的文件（唯一事实来源） */
const ENDPOINT_FILES = new Set(['src/lib/api/endpoints.ts'])

const EMOJI = /[\u{1F000}-\u{1FAFF}\u{1F300}-\u{1F5FF}\u{2600}-\u{27BF}\u{FE0F}\u{2B00}-\u{2BFF}]/u
const HEX_COLOR = /#[0-9a-fA-F]{3,8}\b/
const RGB_COLOR = /\b(?:rgba?|hsla?|oklch)\(/i
const API_LITERAL = /['"`]\/api\//
const VIEWPORT_UNIT = /\b100vh\b|\bh-screen\b/
const ELEMENT_PLUS = /from\s+['"]element-plus['"]|['"]element-plus\/dist/
const INTER_FONT = /\bInter\b/

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      out.push(...walk(full))
    } else {
      out.push(full)
    }
  }
  return out
}

function isComment(line) {
  const trimmed = line.trim()
  return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')
}

const checks = [
  {
    id: 'no-element-plus',
    message: '不得再使用 Element Plus（已从设计系统移除）',
    test: (line) => ELEMENT_PLUS.test(line),
  },
  {
    id: 'no-raw-color',
    message: '不得写裸色值，用 token 语义类（bg-panel / text-ink / border-hairline …）',
    test: (line) => HEX_COLOR.test(line) || RGB_COLOR.test(line),
    skipFiles: TOKEN_FILES,
  },
  {
    id: 'no-emoji',
    message: '不得用 emoji 当图标，用 @phosphor-icons/vue',
    test: (line) => EMOJI.test(line),
  },
  {
    id: 'no-100vh',
    message: '不得用 100vh / h-screen，用 min-h-[100dvh]',
    test: (line) => VIEWPORT_UNIT.test(line),
  },
  {
    id: 'no-api-literal',
    message: '不得在视图/组件里写 /api/ 字面量，统一走 @/lib/api/endpoints',
    test: (line) => API_LITERAL.test(line),
    skipFiles: ENDPOINT_FILES,
  },
  {
    id: 'no-inter-font',
    message: '不得使用 Inter 字体（设计规范指定 Geist）',
    test: (line) => INTER_FONT.test(line),
    skipFiles: TOKEN_FILES,
  },
]

const violations = []
let scanned = 0

for (const file of walk(SRC)) {
  if (!/\.(vue|ts|css)$/.test(file)) {
    continue
  }
  const rel = relative(FRONTEND_ROOT, file)
  const content = readFileSync(file, 'utf8')
  scanned += 1
  content.split('\n').forEach((line, index) => {
    if (isComment(line)) {
      return
    }
    for (const check of checks) {
      if (check.skipFiles?.has(rel)) {
        continue
      }
      if (check.test(line)) {
        violations.push({
          file: rel,
          line: index + 1,
          rule: check.id,
          message: check.message,
          snippet: line.trim().slice(0, 120),
        })
      }
    }
  })
}

if (violations.length > 0) {
  console.error(`\n风格守卫未通过：${violations.length} 处\n`)
  for (const item of violations) {
    console.error(`  ${item.file}:${item.line}  [${item.rule}] ${item.message}`)
    console.error(`      ${item.snippet}`)
  }
  console.error('')
  process.exit(1)
}

console.log(`风格守卫通过（扫描 ${scanned} 个文件，0 处违规）`)
