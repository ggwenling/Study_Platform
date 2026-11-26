import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'

const here = path.dirname(fileURLToPath(import.meta.url))
export const repoRoot = path.resolve(here, '../../..')
export const shotDir = path.join(repoRoot, '.local/ux-acceptance/shots')
export const journalPath = path.join(repoRoot, '.local/ux-acceptance/journey.json')
export const modelBase = process.env.UX_MODEL_BASE_URL || 'http://127.0.0.1:18080/v1'
export const apiOrigin = process.env.UX_API_ORIGIN || 'http://127.0.0.1:8000'
export const appOrigin = process.env.UX_BASE_URL || 'http://127.0.0.1:5173'
export const syntheticPassword = 'UxAccept-94k-local'
export const libraryName = '青柠晶体笔记'
export const emptyLibraryName = '空白对照库'
export const deckTitle = '青柠闪卡'
export const noteTitle = '青柠观察笔记'
export const fixtureName = 'synthetic-concept.txt'
export const question = '青柠晶体在多少度保持稳定？'

export type JourneyEntry = {
  step: string
  status: 'pass' | 'fail'
  ms: number
  note?: string
}

export const journal: JourneyEntry[] = []

export function stamp() {
  return Date.now()
}

export function record(step: string, started: number, status: 'pass' | 'fail', note?: string) {
  journal.push({ step, status, ms: Date.now() - started, note })
  fs.mkdirSync(path.dirname(journalPath), { recursive: true })
  fs.writeFileSync(journalPath, JSON.stringify({ updatedAt: new Date().toISOString(), steps: journal }, null, 2))
}

export async function shot(page: Page, name: string) {
  fs.mkdirSync(shotDir, { recursive: true })
  const file = path.join(shotDir, `${name}.png`)
  await page.screenshot({ path: file, fullPage: true })
  return file
}

export async function attachFailure(page: Page, info: TestInfo) {
  const file = await shot(page, `fail-${info.title.replace(/\s+/g, '-').slice(0, 40)}`)
  await info.attach('failure-screenshot', { path: file, contentType: 'image/png' })
  const body = await page.locator('body').innerText().catch(() => '')
  await info.attach('failure-text', { body, contentType: 'text/plain' })
}

export async function chooseSelect(page: Page, label: string, option: string) {
  await page.getByLabel(label, { exact: true }).click()
  await page.getByRole('option', { name: option, exact: true }).click()
}

export async function register(page: Page, name: string, email: string) {
  await page.goto('/')
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
  await page.getByRole('button', { name: '立即注册' }).click()
  await expect(page.getByRole('heading', { name: '创建账号' })).toBeVisible()
  const dialog = page.getByRole('dialog', { name: '创建账号' })
  await dialog.getByLabel('称呼').fill(name)
  await dialog.getByLabel('邮箱地址').fill(email)
  await dialog.getByRole('textbox', { name: '密码', exact: true }).fill(syntheticPassword)
  await dialog.getByRole('button', { name: '创建账号' }).click()
  await expect(page.getByRole('button', { name: `账户菜单，${name}` })).toBeVisible()
}

export async function loginApi(request: APIRequestContext, email: string) {
  const response = await request.post(`${apiOrigin}/api/v1/auth/login`, {
    headers: { Origin: appOrigin, 'Content-Type': 'application/json' },
    data: { email, password: syntheticPassword },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  const body = await response.json()
  return body.access_token as string
}

export async function authedGet(request: APIRequestContext, token: string, route: string) {
  return request.get(`${apiOrigin}${route}`, {
    headers: { Authorization: `Bearer ${token}`, Origin: appOrigin },
  })
}

export async function libraries(request: APIRequestContext, token: string) {
  const response = await authedGet(request, token, '/api/v1/knowledge-bases')
  expect(response.ok(), await response.text()).toBeTruthy()
  const body = await response.json()
  return body.items as { id: string; name: string }[]
}

export async function waitUntilReady(request: APIRequestContext, token: string, kbId: string) {
  const started = Date.now()
  let last = ''
  while (Date.now() - started < 120_000) {
    const response = await authedGet(request, token, `/api/v1/knowledge-bases/${kbId}/materials`)
    const text = await response.text()
    if (!response.ok()) {
      last = text
    } else {
      const body = JSON.parse(text) as { items: { title: string; status: string; error_code?: string; error_message?: string }[] }
      const item = body.items.find((row) => row.title === fixtureName)
      last = JSON.stringify(item ?? body.items)
      if (item?.status === 'ready') return item
      if (item?.status === 'failed' || item?.status === 'cancelled') {
        throw new Error(`material ${item.status}: ${last}`)
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error(`material was not ready within 120s: ${last}`)
}

export async function mockStats(request: APIRequestContext) {
  const response = await request.get(new URL('/__stats', modelBase).href)
  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<{ embeddings: number; chat: number; chatWithEvidence: number }>
}

export async function addConnection(page: Page, kindLabel: string, name: string, model: string) {
  await page.getByRole('button', { name: '添加连接' }).click()
  const dialog = page.getByRole('dialog', { name: '添加模型连接' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('连接类型').click()
  await page.getByRole('option', { name: kindLabel, exact: true }).click()
  await dialog.getByLabel('连接名称').fill(name)
  await dialog.getByLabel('Base URL').fill(modelBase)
  await dialog.getByLabel('模型名称').fill(model)
  await dialog.getByRole('textbox', { name: 'API Key', exact: true }).fill('ux-local-not-a-secret')
  await dialog.getByRole('button', { name: '保存连接' }).click()
  await expect(dialog).toBeHidden()
  const card = page.getByRole('article', { name: `连接：${name}` })
  await card.getByRole('button', { name: `测试连接：${name}` }).click()
  await expect(card.getByText('已测试', { exact: true })).toBeVisible({ timeout: 30_000 })
  await card.getByRole('button', { name: `设为默认：${name}` }).click()
  await expect(card.getByText('当前默认 · 个人连接')).toBeVisible()
}
