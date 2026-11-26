import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { chooseSelect, modelBase, register } from '../support/journey'

const secondBase = process.env.UX_MODEL_SECOND_BASE_URL || 'http://127.0.0.1:18081/manual/v1'

async function calls(request: APIRequestContext, base: string, model: string) {
  const response = await request.get(new URL('/__stats', base).href)
  expect(response.ok()).toBeTruthy()
  return (await response.json()).chatByModel[model] ?? 0
}

async function send(page: Page, text: string) {
  await page.getByRole('textbox', { name: '提问' }).fill(text)
  await page.getByRole('button', { name: '发送' }).click()
}

test('configures four model types and retries a failed chat only after choosing another model', async ({ page, request }) => {
  await register(page, '模型向导验收', `ux-wizard-${Date.now()}@example.test`)
  const greeting = page.getByRole('region', { name: '接入你的模型' })
  await greeting.getByRole('button', { name: '暂时跳过' }).click()
  await page.reload()
  await expect(page.getByRole('combobox', { name: '聊天模型' })).toBeVisible()
  await expect(page.locator('form.composer').getByRole('combobox', { name: '聊天模型' })).toHaveCount(0)
  await expect(greeting).toHaveCount(0)
  await page.getByRole('navigation', { name: '工作区' }).getByRole('link', { name: '设置' }).click()
  await page.getByRole('button', { name: '配置向导' }).click()
  const wizard = page.getByRole('dialog', { name: '配置个人模型' })
  await expect(wizard).toBeVisible()
  await wizard.getByLabel('供应商', { exact: true }).click()
  await page.getByRole('option', { name: 'DeepSeek', exact: true }).click()
  await expect(wizard.getByLabel('API Base URL')).toHaveValue('https://api.deepseek.com')
  await wizard.getByLabel('供应商', { exact: true }).click()
  await page.getByRole('option', { name: '自定义兼容地址' }).click()
  await wizard.getByLabel('API Base URL').fill(modelBase)
  await wizard.getByLabel('API Key', { exact: true }).fill('ux-model-a-test-key')
  await wizard.getByRole('button', { name: '发现模型' }).click()
  await expect(wizard.getByText('找到 4 个候选模型。')).toBeVisible()
  await expect(wizard.locator('datalist option[value="ux-switch-a"]')).toHaveCount(1)
  await wizard.getByLabel('文本模型 ID').fill('ux-switch-a')
  for (const [role, id] of [['向量', 'ux-embed'], ['视觉', 'ux-vision'], ['重排', 'ux-rerank']]) {
    await wizard.getByRole('checkbox', { name: role }).check()
    await wizard.getByLabel(`${role}模型 ID`).fill(id)
  }
  await wizard.getByRole('button', { name: '保存并测试所选模型' }).click()
  await expect(wizard.getByText('测试通过', { exact: true })).toHaveCount(4)
  await wizard.getByRole('button', { name: '设为默认向量模型', exact: true }).click()
  await expect(wizard.getByRole('button', { name: '已设为默认向量模型' })).toBeVisible()
  await wizard.getByRole('button', { name: '设为默认视觉模型', exact: true }).click()
  await expect(wizard.getByRole('button', { name: '已设为默认视觉模型' })).toBeVisible()
  await wizard.getByRole('button', { name: '关闭' }).click()

  await page.getByRole('button', { name: '配置向导' }).click()
  await wizard.getByLabel('API Base URL').fill(secondBase)
  await wizard.getByLabel('API Key', { exact: true }).fill('ux-model-b-test-key')
  await wizard.getByRole('button', { name: '发现模型' }).click()
  await expect(wizard.getByText('此地址未提供模型列表，请手填模型 ID。')).toBeVisible()
  await wizard.getByLabel('文本模型 ID').fill('ux-switch-b')
  await wizard.getByRole('button', { name: '保存并测试所选模型' }).click()
  await expect(wizard.getByText('测试通过', { exact: true })).toHaveCount(1)
  await wizard.getByRole('button', { name: '关闭' }).click()
  await page.goto('/knowledge')
  await page.getByRole('button', { name: '创建知识库', exact: true }).click()
  const create = page.getByRole('dialog', { name: '创建知识库' })
  await create.getByLabel('名称').fill('模型会话测试库')
  await create.getByRole('button', { name: '创建知识库' }).click()
  await expect(page.getByRole('heading', { level: 1, name: '模型会话测试库', exact: true })).toBeVisible()
  await page.locator('.library-header input[type=file]').setInputFiles({
    name: '库内导入确认.pdf', mimeType: 'application/pdf', buffer: Buffer.from('synthetic pdf'),
  })
  const importDialog = page.getByRole('dialog', { name: '导入资料' })
  await expect(importDialog).toContainText('模型会话测试库')
  await importDialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(importDialog).toBeHidden()
  await page.goto('/')
  await expect(page.getByRole('combobox', { name: '聊天模型' })).toBeVisible()
  await expect(page.locator('form.composer').getByRole('switch')).toHaveCount(0)
  await page.locator('form.composer input[type=file][accept^=".pdf"]').setInputFiles({
    name: '导入确认.pdf', mimeType: 'application/pdf', buffer: Buffer.from('synthetic pdf'),
  })
  await expect(importDialog.getByRole('button', { name: '常规识别并导入' })).toBeFocused()
  await expect(importDialog.getByRole('button', { name: '使用视觉解析并导入' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(importDialog).toBeHidden()
  await chooseSelect(page, '聊天模型', '自定义兼容地址 · 文本 · ux-switch-a')
  await page.getByRole('button', { name: '通用知识补充' }).click()
  await send(page, '确认模型选择')
  await expect(page.getByText('验收回复 ux-switch-a')).toBeVisible()
  await page.reload()
  await expect(page.locator('.chat-header').getByRole('combobox', { name: '聊天模型' })).toContainText('ux-switch-a')
  const supplement = page.getByRole('button', { name: '通用知识补充' })
  if (await supplement.getAttribute('aria-pressed') === 'false') await supplement.click()

  const beforeA = await calls(request, modelBase, 'ux-switch-a')
  const beforeB = await calls(request, secondBase, 'ux-switch-b')
  const failedQuestion = '触发受控失败'
  await send(page, failedQuestion)
  await expect(page.getByText('模型服务拒绝了认证。', { exact: false }).first()).toBeVisible()
  expect(await calls(request, modelBase, 'ux-switch-a')).toBe(beforeA + 1)
  expect(await calls(request, secondBase, 'ux-switch-b')).toBe(beforeB)
  await chooseSelect(page, '聊天模型', '自定义兼容地址 · 文本 · ux-switch-b')
  await expect(page.getByRole('combobox', { name: '聊天模型' })).toContainText('ux-switch-b')
  expect(await calls(request, secondBase, 'ux-switch-b')).toBe(beforeB)
  await page.getByRole('button', { name: '用当前模型重试' }).click()
  await expect(page.getByText('验收回复 ux-switch-b')).toBeVisible()
  await expect(page.getByText(failedQuestion, { exact: true })).toHaveCount(1)
  expect(await calls(request, secondBase, 'ux-switch-b')).toBe(beforeB + 1)
  await page.setViewportSize({ width: 390, height: 844 })
  const modelControl = page.locator('.chat-header').getByRole('combobox', { name: '聊天模型' })
  await expect(modelControl).toBeVisible()
  await modelControl.click()
  await expect(page.getByRole('option', { name: /ux-switch-a/ })).toBeVisible()
})
