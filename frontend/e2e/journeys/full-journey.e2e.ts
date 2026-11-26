import fs from 'node:fs'
import path from 'node:path'
import { expect, test, type BrowserContext, type Page } from '@playwright/test'
import {
  addConnection,
  apiOrigin,
  appOrigin,
  attachFailure,
  authedGet,
  chooseSelect,
  deckTitle,
  emptyLibraryName,
  fixtureName,
  journal,
  journalPath,
  libraries,
  libraryName,
  loginApi,
  mockStats,
  modelBase,
  noteTitle,
  question,
  record,
  register,
  repoRoot,
  shot,
  stamp,
  waitUntilReady,
} from '../support/journey'

const fixturePath = path.join(repoRoot, 'frontend/e2e/fixtures/synthetic-concept.txt')
const videoDir = path.join(repoRoot, '.local/ux-acceptance/video')

let context: BrowserContext
let page: Page
const state = {
  emailA: `ux-a-${Date.now()}@example.test`,
  emailB: `ux-b-${Date.now()}@example.test`,
  contentKbId: '',
  emptyKbId: '',
  conversationId: '',
}

async function runStep(info: { title: string }, name: string, body: () => Promise<string | void>) {
  const started = stamp()
  try {
    const note = await body()
    record(name, started, 'pass', note || undefined)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    record(name, started, 'fail', message)
    if (page) await attachFailure(page, info as never).catch(() => undefined)
    throw error
  }
}

test.describe.serial('isolated synthetic user journey', () => {
  test.beforeAll(async ({ browser }) => {
    for (const url of [`${appOrigin}/`, `${apiOrigin}/health/ready`, new URL('/health', modelBase).href]) {
      const response = await fetch(url)
      if (!response.ok) throw new Error(`${url} returned ${response.status}`)
    }
    fs.mkdirSync(videoDir, { recursive: true })
    context = await browser.newContext({
      locale: 'zh-CN',
      viewport: { width: 1440, height: 900 },
      recordVideo: { dir: videoDir, size: { width: 1440, height: 900 } },
    })
    page = await context.newPage()
    page.on('pageerror', (error) => {
      fs.appendFileSync(path.join(repoRoot, '.local/ux-acceptance/page-errors.log'), `${error.stack || error.message}\n`)
    })
  })

  test.afterAll(async () => {
    await context?.close()
    fs.mkdirSync(path.dirname(journalPath), { recursive: true })
    fs.writeFileSync(journalPath, JSON.stringify({
      updatedAt: new Date().toISOString(),
      accounts: { a: state.emailA, b: state.emailB },
      libraries: { content: state.contentKbId, empty: state.emptyKbId },
      conversationId: state.conversationId,
      model: 'local-mock',
      realModel: 'NOT_VERIFIED',
      steps: journal,
    }, null, 2))
  })

  test('guest is kept out of private routes', async ({}, info) => {
    await runStep(info, 'guest-gates', async () => {
      await page.goto('/knowledge')
      await expect(page).toHaveURL('/')
      await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
      await page.getByRole('button', { name: '关闭登录窗口' }).click()
      await page.goto('/learning')
      await expect(page).toHaveURL('/')
      await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
      await page.getByRole('button', { name: '关闭登录窗口' }).click()
      await page.goto('/chat/demo')
      await expect(page.getByText('示例内容仅用于展示界面。')).toBeVisible()
      await shot(page, '01-guest-demo')
    })
  })

  test('register and keep the session across reload', async ({}, info) => {
    await runStep(info, 'register-and-reload', async () => {
      await register(page, '验收甲', state.emailA)
      await shot(page, '02-registered-home')
      await page.reload()
      await expect(page.getByRole('button', { name: '账户菜单，验收甲' })).toBeVisible()
      const recentArrow = page.getByRole('button', { name: '最近对话', exact: true }).locator('svg')
      const createIcon = page.getByRole('button', { name: '新建项目', exact: true }).locator('svg')
      const centerX = (icon: typeof recentArrow) => icon.evaluate((svg) => {
        const box = svg.getBoundingClientRect()
        return box.left + box.width / 2
      })
      await expect.poll(async () => Math.abs(await centerX(recentArrow) - await centerX(createIcon))).toBeLessThan(1)
    })
  })

  test('point personal model connections at the local stub', async ({}, info) => {
    await runStep(info, 'mock-model-connections', async () => {
      await page.getByRole('navigation', { name: '工作区' }).getByRole('link', { name: '设置' }).click()
      await expect(page.getByRole('heading', { name: '模型连接' })).toBeVisible()
      await addConnection(page, '向量模型 · 1024 维', '本地向量桩', 'ux-embed')
      await addConnection(page, 'LLM 文本模型', '本地回答桩', 'ux-chat')
      await shot(page, '03-model-connections')
      await page.reload()
      await expect(page.getByRole('button', { name: '账户菜单，验收甲' })).toBeVisible()
      await expect(page.getByText('本地回答桩').first()).toBeVisible()
      return modelBase
    })
  })

  test('reject bad uploads and index one synthetic file', async ({ request }, info) => {
    await runStep(info, 'upload-and-index', async () => {
      await page.goto('/knowledge')
      await page.getByRole('button', { name: '创建知识库' }).click()
      const dialog = page.getByRole('dialog', { name: '创建知识库' })
      await dialog.getByLabel('名称').fill(libraryName)
      await dialog.getByRole('button', { name: '创建知识库' }).click()
      await expect(page.getByRole('heading', { level: 1, name: libraryName, exact: true })).toBeVisible()
      state.contentKbId = new URL(page.url()).searchParams.get('id') || ''
      expect(state.contentKbId).toBeTruthy()

      const importButton = page.locator('.library-header').getByRole('button', { name: '导入资料', exact: true })
      const chooseFiles = page.getByRole('button', { name: '选择文件', exact: true })
      await expect(importButton).toBeVisible()
      await expect(chooseFiles).toBeVisible()
      await shot(page, '04-library-empty-import')
      const viewport = page.viewportSize()!
      await page.setViewportSize({ width: 390, height: 844 })
      await expect(importButton).toBeInViewport()
      const [emptyChooser] = await Promise.all([page.waitForEvent('filechooser'), chooseFiles.click()])
      await emptyChooser.setFiles([])
      await page.setViewportSize(viewport)

      const badDir = path.join(repoRoot, '.local/ux-acceptance/tmp')
      fs.mkdirSync(badDir, { recursive: true })
      const emptyFile = path.join(badDir, 'empty.txt')
      const oddFile = path.join(badDir, 'not-a-document.exe')
      fs.writeFileSync(emptyFile, '')
      fs.writeFileSync(oddFile, 'not a document')
      const input = page.locator('input[type="file"]')
      await input.setInputFiles(oddFile)
      await expect(page.getByText('不支持的文件类型：「not-a-document.exe」')).toBeVisible()
      await input.setInputFiles(emptyFile)
      await expect(page.getByText('「empty.txt」内容为空。')).toBeVisible()

      const [chooser] = await Promise.all([page.waitForEvent('filechooser'), importButton.press('Enter')])
      await chooser.setFiles(fixturePath)
      await expect(page.getByText(`「${fixtureName}」已提交至「${libraryName}」`)).toBeVisible()

      const token = await loginApi(request, state.emailA)
      const ready = await waitUntilReady(request, token, state.contentKbId)
      await expect(page.getByRole('tree', { name: '文档层级' })).toContainText(fixtureName)
      await expect(page.getByRole('button', { name: /就绪片段/ })).toContainText(/[1-9]/)
      await shot(page, '04-library-indexed')
      return `status=${ready.status}`
    })
  })

  test('an empty library refuses before calling the stub', async ({ request }, info) => {
    await runStep(info, 'empty-library-refusal', async () => {
      await page.getByRole('button', { name: '知识库操作' }).click()
      await page.getByRole('menuitem', { name: '新建知识库' }).click()
      const dialog = page.getByRole('dialog', { name: '创建知识库' })
      await dialog.getByLabel('名称').fill(emptyLibraryName)
      await dialog.getByRole('button', { name: '创建知识库' }).click()
      await expect(page.getByRole('heading', { level: 1, name: emptyLibraryName, exact: true })).toBeVisible()
      state.emptyKbId = new URL(page.url()).searchParams.get('id') || ''
      const before = await mockStats(request)
      await page.getByRole('button', { name: `在「${emptyLibraryName}」中新建对话` }).click()
      await expect(page.getByRole('region', { name: `当前知识库资料：${emptyLibraryName}` })).toBeVisible()
      const field = page.getByLabel('提问')
      await field.fill('这份空白库里有什么？')
      const namedSend = await page.getByRole('button', { name: '发送' }).count()
      await field.press('Enter')
      await expect(page.getByText('当前资料不足，无法根据所选知识库回答。')).toBeVisible({ timeout: 60_000 })
      const after = await mockStats(request)
      expect(after.chat).toBe(before.chat)
      await shot(page, '05-empty-library-refusal')
      return `chat calls stayed at ${after.chat}; accessible 发送 buttons=${namedSend}`
    })
  })

  test('ask a question and open the cited source', async ({}, info) => {
    await runStep(info, 'chat-and-citation', async () => {
      await page.getByRole('button', { name: `在「${libraryName}」中新建对话` }).click()
      await expect(page.getByRole('region', { name: `当前知识库资料：${libraryName}` })).toBeVisible()
      await page.getByRole('button', { name: '会话操作' }).click()
      await page.getByRole('menuitem', { name: '重命名会话' }).click()
      const rename = page.getByRole('dialog', { name: '重命名会话' })
      await rename.getByLabel('会话标题').fill(question)
      await rename.getByRole('button', { name: '保存' }).click()
      await expect(rename).toBeHidden()
      const field = page.getByLabel('提问')
      await field.fill(question)
      const namedSend = await page.getByRole('button', { name: '发送' }).count()
      await field.press('Enter')
      await page.waitForURL(/\/chat\/[0-9a-f-]{36}/, { timeout: 60_000 })
      state.conversationId = page.url().split('/chat/')[1]
      await expect(page.getByText('概念说明')).toBeVisible({ timeout: 60_000 })
      const citation = page.getByRole('button', { name: /查看引用 S1/ })
      await expect(citation).toBeVisible()
      await citation.click()
      const reader = page.getByRole('dialog', { name: /synthetic-concept\.txt/ })
      await expect(reader).toBeVisible()
      await expect(reader.getByText('行 1').first()).toBeVisible()
      await expect(reader.getByText('37 度下保持稳定')).toBeVisible()
      await shot(page, '06-citation-reader')
      return `conversation=${state.conversationId}; accessible 发送 buttons=${namedSend}; answer is the local stub, not a real model`
    })
  })

  test('new chat uses the recently queried library in a fresh tab', async () => {
    const fresh = await context.newPage()
    try {
      await fresh.goto('/')
      await expect(fresh.getByRole('region', { name: `当前知识库资料：${libraryName}` })).toBeVisible()
      await expect(fresh.locator('.composer-actions').getByRole('combobox', { name: '聊天模型' })).toBeVisible()
      await expect(fresh.locator('.composer-actions').getByRole('combobox', { name: '知识库', exact: true })).toHaveCount(0)
    } finally {
      await fresh.close()
    }
  })

  test('save a note and find it again after reload', async ({}, info) => {
    await runStep(info, 'note-persists', async () => {
      await page.goto(`/knowledge?id=${state.contentKbId}`)
      await page.getByRole('button', { name: '新建笔记' }).click()
      const dialog = page.getByRole('dialog', { name: '新建研究笔记' })
      await dialog.getByLabel('笔记标题').fill(noteTitle)
      await dialog.getByLabel('Markdown 内容').fill('合成记录：青柠晶体只存在于本验收夹具，并在 37 度下保持稳定。')
      await dialog.getByRole('button', { name: '保存笔记' }).click()
      const saved = page.getByRole('dialog', { name: noteTitle })
      await expect(saved.getByText('只存在于本验收夹具')).toBeVisible()
      await saved.getByRole('button', { name: '关闭笔记详情' }).click()
      await expect(page.getByRole('button', { name: new RegExp(noteTitle) })).toBeVisible()
      await page.reload()
      await expect(page.getByRole('button', { name: new RegExp(noteTitle) })).toBeVisible()
      await page.getByRole('button', { name: new RegExp(noteTitle) }).click()
      await expect(page.getByRole('dialog', { name: noteTitle }).getByText('只存在于本验收夹具')).toBeVisible()
      await shot(page, '07-note')
    })
  })

  test('move across history, search, learning and settings', async ({}, info) => {
    await runStep(info, 'navigation', async () => {
      await page.keyboard.press('Escape')
      await page.getByRole('button', { name: '全局搜索' }).click()
      await page.getByRole('dialog', { name: '搜索工作区' }).getByRole('combobox', { name: '搜索关键词' }).fill('青柠晶体')
      await expect(page.getByRole('option').filter({ hasText: '青柠' }).first()).toBeVisible()
      await page.keyboard.press('Escape')
      const nav = page.getByRole('navigation', { name: '工作区' })
      await nav.getByRole('link', { name: '历史' }).click()
      await expect(page.getByRole('heading', { name: '会话历史' })).toBeVisible()
      const historyLink = page.locator('.history-page').getByRole('link', { name: question, exact: true })
      await expect(historyLink).toBeVisible()
      await expect(historyLink).toHaveAttribute('href', `/chat/${state.conversationId}`)
      await nav.getByRole('link', { name: '学习' }).click()
      await expect(page.getByRole('tab', { name: '练习', exact: true })).toBeVisible()
      await nav.getByRole('link', { name: '设置' }).click()
      await expect(page.getByRole('heading', { name: '设置' })).toBeVisible()
      await nav.getByRole('link', { name: '新对话' }).click()
      await expect(page.getByLabel('提问')).toBeVisible()
      await shot(page, '08-navigation-home')
    })
  })

  test('generate stub flashcards and keep the review after reload', async ({}, info) => {
    await runStep(info, 'flashcard-review-persists', async () => {
      await page.getByRole('navigation', { name: '工作区' }).getByRole('link', { name: '学习' }).click()
      await page.getByRole('tab', { name: /闪卡复习/ }).click()
      await chooseSelect(page, '选择知识库', libraryName)
      await page.getByLabel('卡组标题').fill(deckTitle)
      await page.getByRole('button', { name: '从当前库生成闪卡' }).click()
      await expect(page.getByRole('button', { name: '开始翻转复习' })).toBeVisible({ timeout: 90_000 })
      await page.getByRole('button', { name: '开始翻转复习' }).click()
      await expect(page.getByRole('article', { name: '闪卡复习卡片' })).toBeVisible()
      await expect(page.getByText('已复习 0 次')).toBeVisible()
      await page.getByRole('button', { name: '翻转查看答案 [Space]' }).click()
      await page.getByRole('button', { name: '良好 [3]' }).click()
      await expect(page.getByRole('article', { name: '本轮复习总结' })).toBeVisible()
      await shot(page, '09-review-summary')
      await page.getByRole('button', { name: '返回卡组列表' }).click()
      await expect(page.getByText('待复习 0 张')).toBeVisible()
      await page.reload()
      await page.getByRole('tab', { name: /闪卡复习/ }).click()
      await expect(page.getByText('待复习 0 张')).toBeVisible()
      await page.getByRole('button', { name: '开始翻转复习' }).click()
      await expect(page.getByText('本组今日到期卡片已全部复习完毕')).toBeVisible()
      await page.getByRole('button', { name: '自由重温全部卡片' }).click()
      await expect(page.getByText('已复习 1 次')).toBeVisible()
      await shot(page, '10-review-persisted')
      return 'LEARNING_STUB_GRADER cards; real generation quality NOT_VERIFIED'
    })
  })

  test('a second account cannot see the first account', async ({ browser, request }, info) => {
    await runStep(info, 'cross-user-isolation', async () => {
      const tokenA = await loginApi(request, state.emailA)
      const other = await browser.newContext({ locale: 'zh-CN', viewport: { width: 1440, height: 900 } })
      const otherPage = await other.newPage()
      try {
        await register(otherPage, '验收乙', state.emailB)
        await otherPage.goto('/knowledge')
        await expect(otherPage.getByText(libraryName)).toHaveCount(0)
        await expect(otherPage.getByText('暂无知识库')).toBeVisible()
        await otherPage.goto(`/knowledge?id=${state.contentKbId}`)
        await expect(otherPage.getByText('知识库不可用')).toBeVisible()
        await otherPage.goto(`/chat/${state.conversationId}`)
        await expect(otherPage.getByText('无法打开会话')).toBeVisible()
        await shot(otherPage, '11-cross-user')
      } finally {
        await other.close()
      }
      const tokenB = await loginApi(request, state.emailB)
      const hiddenLibrary = await authedGet(request, tokenB, `/api/v1/knowledge-bases/${state.contentKbId}`)
      const hiddenChat = await authedGet(request, tokenB, `/api/v1/conversations/${state.conversationId}`)
      const ownLibraries = await libraries(request, tokenB)
      const stillThere = await authedGet(request, tokenA, `/api/v1/knowledge-bases/${state.contentKbId}`)
      expect(hiddenLibrary.status()).toBe(404)
      expect(hiddenChat.status()).toBe(404)
      expect(ownLibraries.map((item) => item.name)).not.toContain(libraryName)
      expect(stillThere.ok()).toBeTruthy()
      return `library ${hiddenLibrary.status()}, chat ${hiddenChat.status()}`
    })
  })

  test('logout sends private routes back to the login dialog', async ({}, info) => {
    await runStep(info, 'logout', async () => {
      await page.goto('/')
      await page.getByRole('button', { name: '账户菜单，验收甲' }).click()
      await page.getByRole('menuitem', { name: '退出登录' }).click()
      await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
      await page.goto('/knowledge')
      await expect(page).toHaveURL('/')
      await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
      await shot(page, '12-logged-out')
    })
  })
})
