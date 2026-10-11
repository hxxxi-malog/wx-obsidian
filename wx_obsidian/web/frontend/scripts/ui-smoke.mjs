#!/usr/bin/env node
/**
 * 浏览器冒烟（UI smoke）：对正在运行的服务跑一遍「8 个页面 + 关键交互」的断言。
 *
 * 为什么不用 playwright/puppeteer：这个项目是本地工具，不该为一次验收引入 300MB 的
 * 浏览器依赖。Node 22+ 自带 WebSocket，直接用 Chrome DevTools Protocol 就够。
 *
 * 用法：
 *   node scripts/ui-smoke.mjs --base http://127.0.0.1:8765 --token <token> [--out <dir>] [--headed]
 *   也可用环境变量：WX_UI_BASE / WX_UI_TOKEN
 * 不带 --token 时仍会跑：此时断言的是「未授权引导」而不是白屏（这本身是回归项）。
 *
 * 退出码：0 全部通过；1 有断言失败；2 环境不具备（没找到 Chrome）。
 */

import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawn } from 'node:child_process'

const CHROME_CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
]

const ROUTES = [
  { path: '/', label: '状态总览' },
  { path: '/run', label: '文章抓取' },
  { path: '/articles', label: '文章管理' },
  { path: '/settings', label: '配置管理' },
  { path: '/feeds', label: '公众号管理' },
  { path: '/source', label: '数据源状态' },
  { path: '/account', label: '账号管理' },
  { path: '/scheduler', label: '定时任务' },
]

function parseArgs(argv) {
  const args = { base: process.env.WX_UI_BASE || 'http://127.0.0.1:8765', token: process.env.WX_UI_TOKEN || '', out: '', headed: false, port: 9333 }
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index]
    if (item === '--base') args.base = argv[++index]
    else if (item === '--token') args.token = argv[++index]
    else if (item === '--out') args.out = argv[++index]
    else if (item === '--port') args.port = Number(argv[++index])
    else if (item === '--headed') args.headed = true
  }
  return args
}

const args = parseArgs(process.argv.slice(2))
const results = []
let currentStep = '启动'

function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  const mark = ok ? 'PASS' : 'FAIL'
  console.log(`  [${mark}] ${name}${detail ? ` — ${detail}` : ''}`)
  if (!ok) {
    process.exitCode = 1
  }
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms))

function findChrome() {
  return CHROME_CANDIDATES.find((path) => existsSync(path)) || ''
}

async function fetchJson(url, options) {
  const response = await fetch(url, options)
  return response.json()
}

class Cdp {
  constructor(socket) {
    this.socket = socket
    this.seq = 0
    this.pending = new Map()
    this.listeners = []
    socket.addEventListener('message', (event) => {
      const payload = JSON.parse(event.data)
      if (payload.id && this.pending.has(payload.id)) {
        const { resolve: done, reject } = this.pending.get(payload.id)
        this.pending.delete(payload.id)
        if (payload.error) {
          reject(new Error(`${payload.error.message} (${currentStep})`))
        } else {
          done(payload.result)
        }
        return
      }
      this.listeners.forEach((listener) => listener(payload))
    })
  }

  on(listener) {
    this.listeners.push(listener)
  }

  send(method, params = {}, sessionId) {
    this.seq += 1
    const id = this.seq
    const message = { id, method, params }
    if (sessionId) {
      message.sessionId = sessionId
    }
    return new Promise((done, reject) => {
      this.pending.set(id, { resolve: done, reject })
      this.socket.send(JSON.stringify(message))
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id)
          reject(new Error(`CDP 超时：${method}（${currentStep}）`))
        }
      }, 30000)
    })
  }
}

async function main() {
  const chromePath = findChrome()
  if (!chromePath) {
    console.error('未找到 Chrome/Chromium，跳过 UI 冒烟（请安装 Chrome 或设置 WX_UI_CHROME）')
    process.exit(2)
  }

  const profile = mkdtempSync(join(tmpdir(), 'wx-ui-smoke-'))
  const chrome = spawn(
    chromePath,
    [
      ...(args.headed ? [] : ['--headless=new']),
      '--no-sandbox',
      '--disable-gpu',
      '--disable-crash-reporter',
      '--disable-breakpad',
      '--no-first-run',
      '--no-default-browser-check',
      '--hide-scrollbars',
      `--user-data-dir=${profile}`,
      `--remote-debugging-port=${args.port}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  let wsUrl = ''
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const version = await fetchJson(`http://127.0.0.1:${args.port}/json/version`)
      wsUrl = version.webSocketDebuggerUrl
      break
    } catch {
      await sleep(250)
    }
  }
  if (!wsUrl) {
    console.error('无法连上 Chrome 调试端口')
    chrome.kill()
    process.exit(2)
  }

  const socket = new WebSocket(wsUrl)
  await new Promise((done, reject) => {
    socket.addEventListener('open', done)
    socket.addEventListener('error', reject)
  })
  const cdp = new Cdp(socket)

  const consoleErrors = []
  const exceptions = []
  const target = await cdp.send('Target.createTarget', { url: 'about:blank' })
  const attached = await cdp.send('Target.attachToTarget', { targetId: target.targetId, flatten: true })
  const session = attached.sessionId

  cdp.on((payload) => {
    if (payload.sessionId !== session) {
      return
    }
    if (payload.method === 'Runtime.exceptionThrown') {
      exceptions.push(payload.params.exceptionDetails?.exception?.description ?? 'unknown exception')
    }
    if (payload.method === 'Runtime.consoleAPICalled' && payload.params.type === 'error') {
      consoleErrors.push(payload.params.args.map((arg) => arg.value ?? arg.description ?? '').join(' '))
    }
  })

  await cdp.send('Page.enable', {}, session)
  await cdp.send('Runtime.enable', {}, session)
  await cdp.send('Network.enable', {}, session)
  // 固定桌面视口：截图矩阵要与改前对照，尺寸必须一致
  await cdp.send(
    'Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false },
    session,
  )

  async function evaluate(expression) {
    const result = await cdp.send(
      'Runtime.evaluate',
      { expression, returnByValue: true, awaitPromise: true },
      session,
    )
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text ?? 'evaluate 失败')
    }
    return result.result.value
  }

  /**
   * 打开路由并等到「页面真的画出来」。
   *
   * 只等 `#app > *` 是不够的：视图是懒加载的，外壳（顶栏/侧栏）先渲染，
   * 这时截图会拍到空白内容区。所以额外等 h1 的文案等于该页标题——
   * 那是每个视图都有的锚点（PageHeader 固定渲染 h1）。
   */
  async function goto(path, { expectHeading = '' } = {}) {
    const url = `${args.base}${path}${args.token ? `${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(args.token)}` : ''}`
    await cdp.send('Page.navigate', { url }, session)
    for (let attempt = 0; attempt < 100; attempt += 1) {
      await sleep(150)
      try {
        const ready = await evaluate('document.readyState === "complete" && !!document.querySelector("#app > *")')
        if (!ready) {
          continue
        }
        if (!expectHeading) {
          return
        }
        const heading = await evaluate('document.querySelector("h1")?.innerText?.trim() ?? ""')
        if (heading === expectHeading) {
          return
        }
      } catch {
        // 导航过程中 evaluate 可能失败，继续等
      }
    }
  }

  /** 等骨架屏退场（最多 5s），截图才有信息量 */
  async function settle() {
    for (let attempt = 0; attempt < 25; attempt += 1) {
      try {
        const pending = await evaluate('document.querySelectorAll("[aria-label=\\"加载中\\"]").length')
        if (pending === 0) {
          return
        }
      } catch {
        return
      }
      await sleep(200)
    }
  }

  async function screenshot(name) {
    if (!args.out) {
      return
    }
    await settle()
    const shot = await cdp.send('Page.captureScreenshot', { format: 'png' }, session)
    mkdirSync(args.out, { recursive: true })
    writeFileSync(join(args.out, `${name}.png`), Buffer.from(shot.data, 'base64'))
  }

  async function key(keyName, modifiers = 0) {
    const code = keyName === 'k' ? 'KeyK' : keyName
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: keyName, code, modifiers, windowsVirtualKeyCode: keyName === 'k' ? 75 : 0 }, session)
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: keyName, code, modifiers, windowsVirtualKeyCode: keyName === 'k' ? 75 : 0 }, session)
    await sleep(250)
  }

  try {
    // ── 1. 逐页加载：标题、h1、无异常 ───────────────────────────────
    currentStep = '逐页加载'
    for (const route of ROUTES) {
      consoleErrors.length = 0
      exceptions.length = 0
      await goto(route.path, { expectHeading: route.label })
      const title = await evaluate('document.title')
      check(`${route.path} 文档标题`, title === `${route.label} · wx-obsidian`, title)
      const heading = await evaluate('document.querySelector("h1")?.innerText?.trim() ?? ""')
      check(`${route.path} 页面标题`, heading === route.label, heading)
      check(`${route.path} 无未捕获异常`, exceptions.length === 0, exceptions[0] ?? '')
      const httpErrors = consoleErrors.filter((line) => !/favicon|ERR_/.test(line))
      check(`${route.path} 无 console.error`, httpErrors.length === 0, httpErrors[0] ?? '')
      await screenshot(`route-${route.path === '/' ? 'dashboard' : route.path.slice(1)}`)
    }

    // ── 2. token 处理 ──────────────────────────────────────────────
    currentStep = 'token 处理'
    await goto('/')
    const url = await evaluate('location.href')
    if (args.token) {
      check('地址栏已抹掉 token', !url.includes('token='), url)
      const stored = await evaluate('sessionStorage.getItem("wx-obsidian-token") ?? ""')
      check('token 已写入 sessionStorage', stored === args.token)
      const dataLoaded = await evaluate(
        'document.body.innerText.includes("已处理") || document.body.innerText.includes("暂")',
      )
      check('首屏拿到后端数据', dataLoaded)
    } else {
      const guidance = await evaluate(
        'document.body.innerText.includes("未授权") || document.body.innerText.includes("token")',
      )
      check('无 token 时给出未授权引导（不是白屏）', guidance)
    }

    // ── 3. 主题切换 ─────────────────────────────────────────────────
    // 轮转是 system → dark → light → system；系统本身是暗色时第一次点击不会改变 html.dark，
    // 所以断言的是「模式轮转 + html.dark 与该模式一致」，而不是「类名一定翻转」。
    currentStep = '主题切换'
    await goto('/')
    const modeBefore = await evaluate('localStorage.getItem("wx-obsidian-theme") ?? "system"')
    await evaluate('document.querySelector("[aria-label=\\"切换主题\\"]").click()')
    await sleep(250)
    const modeAfter = await evaluate('localStorage.getItem("wx-obsidian-theme") ?? "system"')
    const expected = modeBefore === 'system' ? 'dark' : modeBefore === 'dark' ? 'light' : 'system'
    check('主题按钮按 system→dark→light 轮转', modeAfter === expected, `${modeBefore} → ${modeAfter}`)
    const isDark = await evaluate('document.documentElement.classList.contains("dark")')
    const systemDark = await evaluate('window.matchMedia("(prefers-color-scheme: dark)").matches')
    const wantDark = expected === 'dark' || (expected === 'system' && systemDark)
    check('html.dark 与所选模式一致', isDark === wantDark, `mode=${expected} dark=${isDark}`)

    // ── 4. 命令面板 ─────────────────────────────────────────────────
    currentStep = '命令面板'
    await goto('/')
    await key('k', 4) // meta
    const paletteOpen = await evaluate('!!document.querySelector("[role=combobox][aria-controls=palette-list]")')
    check('⌘K 打开命令面板', paletteOpen)
    if (paletteOpen) {
      await screenshot('palette')
      await key('Escape')
      const closed = await evaluate('!document.querySelector("[role=combobox][aria-controls=palette-list]")')
      check('Esc 关闭命令面板', closed)
    }

    // ── 5. 文章抽屉 ────────────────────────────────────────────────
    currentStep = '文章详情抽屉'
    if (args.token) {
      await goto('/articles')
      const rows = await evaluate('document.querySelectorAll("tbody tr").length')
      if (rows > 0) {
        await evaluate('document.querySelector("tbody tr").click()')
        await sleep(400)
        const drawer = await evaluate('!!document.querySelector("[role=dialog]")')
        check('点击行打开详情抽屉', drawer)
        if (drawer) {
          await screenshot('article-drawer')
          await key('Escape')
          const closed = await evaluate('!document.querySelector("[role=dialog]")')
          check('Esc 关闭抽屉', closed)
        }
      } else {
        check('文章表格有数据', false, '表格为空，无法验证抽屉')
      }
    }

    // ── 6. 移动端无横向滚动 ────────────────────────────────────────
    currentStep = '移动端布局'
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, session)
    for (const route of ROUTES) {
      await goto(route.path, { expectHeading: route.label })
      const overflow = await evaluate(
        'document.documentElement.scrollWidth - document.documentElement.clientWidth',
      )
      check(`${route.path} 390px 无横向溢出`, overflow <= 1, `${overflow}px`)
    }
    await screenshot('mobile-dashboard')
    if (args.token) {
      currentStep = '移动端文章页'
      await goto('/articles')
      await screenshot('mobile-articles')
    }
    await cdp.send('Emulation.clearDeviceMetricsOverride', {}, session)

    // ── 7. 浅色主题截图（暗色在逐页加载时已拍） ─────────────────────
    currentStep = '浅色主题截图'
    // 清掉移动端 override 后 Chrome 会回到默认小窗，浅色矩阵要重新固定桌面视口
    await cdp.send(
      'Emulation.setDeviceMetricsOverride',
      { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false },
      session,
    )
    await evaluate('localStorage.setItem("wx-obsidian-theme", "light")')
    for (const route of ROUTES) {
      await goto(route.path, { expectHeading: route.label })
      await screenshot(`light-${route.path === '/' ? 'dashboard' : route.path.slice(1)}`)
    }
    await evaluate('localStorage.removeItem("wx-obsidian-theme")')
  } catch (err) {
    check(`${currentStep} 执行未抛错`, false, err instanceof Error ? err.message : String(err))
  } finally {
    socket.close()
    if (!args.headed) {
      await fetchJson(`http://127.0.0.1:${args.port}/json/close/${target.targetId}`).catch(() => undefined)
    }
    chrome.kill()
  }

  const failed = results.filter((item) => !item.ok)
  console.log(`\nUI 冒烟：${results.length - failed.length}/${results.length} 通过`)
  if (args.out) {
    console.log(`截图目录：${resolve(args.out)}`)
  }
  if (failed.length > 0) {
    console.log('失败项：')
    failed.forEach((item) => console.log(`  - ${item.name}${item.detail ? ` — ${item.detail}` : ''}`))
    process.exit(1)
  }
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
