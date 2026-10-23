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
  { path: '/latest', label: '最新文章' },
  { path: '/articles', label: '文章管理' },
  { path: '/settings', label: '配置管理' },
  { path: '/feeds', label: '公众号管理' },
  { path: '/source', label: '数据源状态' },
  { path: '/account', label: '账号管理' },
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

  /**
   * 轮询一个返回布尔值的表达式。
   * 整页刷新/导航期间 evaluate 会失败，每次都是「还没好」，继续等即可。
   */
  async function waitFor(expression, timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      try {
        if (await evaluate(expression)) {
          return true
        }
      } catch {
        // 页面正在导航：继续等
      }
      await sleep(120)
    }
    return false
  }

  /** 给受控输入框赋值并触发 Vue 的 v-model（CDP 里最省事的等价于「用户粘贴」） */
  function setInputScript(selector, value) {
    return `(() => {
      const el = document.querySelector(${JSON.stringify(selector)})
      if (!el) return false
      el.value = ${JSON.stringify(value)}
      el.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    })()`
  }

  /**
   * 关掉自动弹出的令牌解锁对话框。
   *
   * 无 token 模式下它每次导航后都会出现，而且它是**模态**的（锁页面滚动）——
   * 滚动类断言必须先点「稍后」把它收起来，否则 window.scrollTo 会被吃掉。
   * 有 token 模式下没有这个对话框，这里是 no-op。
   */
  async function dismissAuthDialog() {
    let open = false
    try {
      open = await evaluate('!!document.querySelector("[data-token-dialog]")')
    } catch {
      return
    }
    if (!open) {
      return
    }
    // 关闭按钮在对话框头部（不在 [data-token-dialog] 这个内容容器里），按 role=dialog 往上找
    await evaluate(`(() => {
      const input = document.querySelector('[data-token-input]')
      const dialog = input ? input.closest('[role="dialog"]') : null
      if (!dialog) return false
      const button =
        dialog.querySelector('[aria-label="关闭"]') ??
        [...dialog.querySelectorAll('button')].find((item) => item.textContent.trim() === '稍后')
      if (!button) return false
      button.click()
      return true
    })()`)
    await waitFor('!document.querySelector("[data-token-dialog]")', 4000)
    await sleep(150) // 等 reka 解除滚动锁
  }

  /**
   * 打开一个**确实能滚动**的页面（顶栏遮挡断言的前提）。
   *
   * 无 token 模式下仪表盘没有数据，整页比视口还矮（实测 scrollHeight 469px @1000px 视口），
   * scrollTo(0, 40) 会被夹回 0，遮罩断言就变成假失败。短页面时退到 /settings。
   */
  async function gotoScrollablePage() {
    await goto('/', { expectHeading: '状态总览' })
    await settle()
    await dismissAuthDialog()
    const room = await evaluate('document.documentElement.scrollHeight - window.innerHeight')
    if (room > 40) {
      return
    }
    await goto('/settings', { expectHeading: '配置管理' })
    await settle()
    await dismissAuthDialog()
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
      if (route.path === '/latest' && args.token) {
        // 只验证页面标题不够：这个页面的价值全在「真的列出文章 + 链接可点」
        const rowsReady = await waitFor(
          'document.querySelectorAll(\'main a[target="_blank"]\').length > 0',
          10000,
        )
        check('/latest 渲染出文章行', rowsReady)
        if (rowsReady) {
          const rows = await evaluate(
            'document.querySelectorAll(\'main a[target="_blank"]\').length',
          )
          const originalLinks = await evaluate(
            '[...document.querySelectorAll(\'main a[target="_blank"]\')].every((a) => (a.getAttribute("href") || "").startsWith("https://mp.weixin.qq.com/"))',
          )
          const dayGroups = await evaluate('document.querySelectorAll("main section h2").length')
          check('/latest 每行都指向公众号原文', originalLinks, `${rows} 行`)
          check('/latest 按日期分组', dayGroups >= 1, `${dayGroups} 组`)
        }
      }
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
      const gateHidden = await evaluate('!document.querySelector("[data-token-gate]")')
      check('已鉴权时不显示未授权横幅', gateHidden)
    } else {
      const guidance = await evaluate(
        'document.body.innerText.includes("未授权") || document.body.innerText.includes("token")',
      )
      check('无 token 时给出未授权引导（不是白屏）', guidance)
      const gateShown = await evaluate('!!document.querySelector("[data-token-gate]")')
      check('无 token 时展示未授权横幅', gateShown)
      const dialogShown = await evaluate('!!document.querySelector("[data-token-dialog]")')
      check('无 token 时自动弹出解锁对话框', dialogShown)
      const inputShown = await evaluate('!!document.querySelector("[data-token-input]")')
      check('解锁对话框有 token 输入框', inputShown)
      await dismissAuthDialog()
      check('解锁对话框可关闭（等价于点「稍后」）', !(await evaluate('!!document.querySelector("[data-token-dialog]")')))
      check('关掉对话框后未授权横幅仍在', await evaluate('!!document.querySelector("[data-token-gate]")'))
    }

    // ── 2b. 裸地址 → 粘贴 token 解锁（恢复链路真跑一遍）─────────────
    // 这是「打开 localhost:8765 一片 401」那个坑的回归项：应用要能自救，
    // 用户不必去翻终端找地址。
    if (args.token) {
      currentStep = 'token 解锁恢复'
      await evaluate('sessionStorage.removeItem("wx-obsidian-token")')
      await cdp.send('Page.navigate', { url: `${args.base}/` }, session)
      check('裸地址进入时出现未授权横幅', await waitFor('!!document.querySelector("[data-token-gate]")'))
      check('裸地址进入时自动弹出解锁对话框', await waitFor('!!document.querySelector("[data-token-dialog]")'))
      const inputShown = await evaluate('!!document.querySelector("[data-token-input]")')
      check('解锁对话框有 token 输入框', inputShown)
      if (inputShown) {
        await screenshot('token-gate')

        await evaluate(setInputScript('[data-token-input]', 'definitely-wrong'))
        await evaluate('document.querySelector("[data-token-submit]").click()')
        check('错误 token 被明确拒绝', await waitFor('document.body.innerText.includes("不被服务接受")'))

        // 粘贴「启动器打印的整条地址」——这是用户最可能复制的形式，也验证地址解析
        await evaluate(setInputScript('[data-token-input]', `${args.base}/?token=${args.token}`))
        await evaluate('document.querySelector("[data-token-submit]").click()')
        // 解锁后会整页刷新：等到「数据回来 + 横幅消失」两个条件同时成立
        const recovered = await waitFor(
          '(() => { const body = document.body.innerText; return !document.querySelector("[data-token-gate]") && (body.includes("已处理") || body.includes("暂")) })()',
          15000,
        )
        check('粘贴正确 token 后解锁并恢复数据', recovered)
        const stored = await evaluate('sessionStorage.getItem("wx-obsidian-token") ?? ""')
        check('解锁后的 token 写入 sessionStorage', stored === args.token)
      }
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

    // ── 8. 粘性条毛玻璃：滚动时内容从下方穿过，不能出现硬切边 ────────
    // 契约：静止（y=0）时遮罩不绘制（零成本）；一旦滚动，遮罩可见且模糊层完全不透明；
    // 顶栏始终吸顶；底部的保存条遮罩常显。
    // y=40 是「标题正好卡在栏底」的位置，也正是当初最难看的那一幕；
    // 深处那一枪打在 /settings（长页面），顺带覆盖保存条。
    currentStep = '粘性条毛玻璃'
    const veilProbe = `(() => {
      const veil = document.querySelector('[data-topbar-veil]')
      if (!veil) return { missing: true }
      const band = veil.querySelector('[data-veil-band]')
      const header = veil.closest('header')
      return {
        state: veil.dataset.veil,
        visibility: getComputedStyle(veil).visibility,
        bandOpacity: band ? Number(getComputedStyle(band).opacity) : -1,
        headerTop: header ? Math.round(header.getBoundingClientRect().top) : -1,
        y: Math.round(window.scrollY),
      }
    })()`
    const saveBarProbe = `(() => {
      const veil = document.querySelector('[data-savebar-veil]')
      if (!veil) return { missing: true }
      const band = veil.querySelector('[data-veil-band]')
      return {
        visibility: getComputedStyle(veil).visibility,
        bandOpacity: band ? Number(getComputedStyle(band).opacity) : -1,
        height: Math.round(veil.getBoundingClientRect().height),
      }
    })()`

    await evaluate('localStorage.setItem("wx-obsidian-theme", "dark")')
    await gotoScrollablePage() // 无 token 模式仪表盘比视口矮，这里会退到 /settings
    const resting = await evaluate(veilProbe)
    check('静止时顶栏遮罩不绘制', !!resting && resting.visibility === 'hidden', JSON.stringify(resting))

    await evaluate('window.scrollTo(0, 40)')
    await sleep(350)
    const scrolled = await evaluate(veilProbe)
    check(
      '滚动后顶栏遮罩出现',
      !!scrolled && scrolled.state === 'on' && scrolled.visibility === 'visible',
      JSON.stringify(scrolled),
    )
    check('滚动后模糊层完全不透明', !!scrolled && scrolled.bandOpacity > 0.95, String(scrolled?.bandOpacity))
    check('顶栏保持吸顶', !!scrolled && scrolled.headerTop === 0, String(scrolled?.headerTop))
    await screenshot('scrolled-topbar')

    await goto('/settings', { expectHeading: '配置管理' })
    await settle()
    await dismissAuthDialog()
    await evaluate('window.scrollTo(0, 600)')
    await sleep(350)
    const deep = await evaluate(veilProbe)
    check('长页面深处遮罩仍可见', !!deep && deep.state === 'on' && deep.visibility === 'visible', JSON.stringify(deep))
    const saveBar = await evaluate(saveBarProbe)
    check(
      '底部保存条遮罩常显',
      !!saveBar && saveBar.visibility === 'visible' && saveBar.bandOpacity > 0.95 && saveBar.height > 60,
      JSON.stringify(saveBar),
    )
    await screenshot('scrolled-topbar-deep')

    await evaluate('localStorage.setItem("wx-obsidian-theme", "light")')
    await gotoScrollablePage()
    await evaluate('window.scrollTo(0, 40)')
    await sleep(350)
    const light = await evaluate(veilProbe)
    check('浅色主题下遮罩同样生效', !!light && light.state === 'on' && light.visibility === 'visible', JSON.stringify(light))
    await screenshot('scrolled-topbar-light')

    currentStep = '浅色保存条'
    await goto('/settings', { expectHeading: '配置管理' })
    await settle()
    await dismissAuthDialog()
    await evaluate('window.scrollTo(0, 600)')
    await sleep(350)
    await screenshot('savebar-light')
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
