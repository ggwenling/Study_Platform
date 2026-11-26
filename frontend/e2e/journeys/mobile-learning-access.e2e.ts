import { expect, test, type Locator } from '@playwright/test'

const library = { id: 'kb-1', name: '演示知识库', description: null, created_at: '', updated_at: '' }
const conversation = { id: '00000000-0000-4000-8000-000000000001', title: '演示会话', project_knowledge_base_id: library.id, created_at: '', updated_at: '' }
const attempts = {
  items: [{
    id: 'attempt-1', quiz_id: 'quiz-1', title: '简答练习', knowledge_base_id: library.id, knowledge_base_name: library.name,
    status: 'in_progress', item_count: 1, answered_count: 0, correct_count: 0,
    started_at: '2026-11-26T08:00:00Z', updated_at: '2026-11-26T08:00:00Z',
  }],
  next_cursor: null,
}
const emptyPage = { items: [], next_cursor: null }
const scope = { knowledge_base_id: library.id, knowledge_base_name: library.name, answer_mode: 'knowledge_only', index_version_ids: [] }

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    if (path === '/chat/stream') {
      const event = { type: 'meta', conversation_id: conversation.id, turn_id: 'turn-1', user_message_id: 'user-1', assistant_message_id: 'assistant-1', scope }
      return route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify(event)}\n\n` })
    }
    const responses: Record<string, unknown> = {
      '/auth/csrf': { csrf_token: 'fixture' },
      '/auth/refresh': { user: { id: 'user-1', display_name: '验收用户', email: 'fixture@example.test', avatar_url: null }, access_token: 'fixture', csrf_token: 'fixture' },
      '/capabilities': {
        rag: true, library_rag: { [library.id]: true }, has_personal_models: true,
        chat_models: [{ id: 'model-1', name: '演示连接', model: 'long-model-name-for-narrow-touch-layout', status: 'ready', is_default: true }],
      },
      '/dashboard/overview': { knowledge_base_count: 1, material_count: 0, completed_qa_count: 0, activity: [] },
      '/knowledge-bases': { items: [library], next_cursor: null },
      '/conversations': { items: [conversation], next_cursor: null },
      [`/conversations/${conversation.id}`]: conversation,
      [`/conversations/${conversation.id}/turns/turn-1`]: { conversation_id: conversation.id, turn_id: 'turn-1', status: 'streaming', scope, messages: [] },
      [`/conversations/${conversation.id}/turns/turn-1/cancel`]: { conversation_id: conversation.id, turn_id: 'turn-1', status: 'stopped', scope, messages: [] },
      '/learning/attempts': attempts,
      '/learning/attempts/attempt-1': { ...attempts.items[0], items: [{
        response_id: 'response-1', quiz_item_id: 'item-1', ordinal: 0, stem: '这是一道简答题。', item_type: 'short_answer',
        options: [], sources: [], answer: { type: 'pending' }, verdict: null,
      }] },
    }
    await route.fulfill({ json: responses[path] ?? emptyPage })
  })
})

async function expectSeparateControls(model: Locator, action: Locator) {
  const [modelBox, actionBox] = await Promise.all([model.boundingBox(), action.boundingBox()])
  expect(modelBox).not.toBeNull()
  expect(actionBox).not.toBeNull()
  expect(modelBox!.x + modelBox!.width).toBeLessThanOrEqual(actionBox!.x)
  for (const control of [model, action]) {
    expect(await control.evaluate((el) => {
      const rect = el.getBoundingClientRect()
      return el.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2))
    })).toBe(true)
  }
}

for (const width of [320, 390]) {
  test.describe(`触屏 ${width}px`, () => {
    test.use({ viewport: { width, height: 844 }, hasTouch: true, reducedMotion: 'reduce' })

    test('首页从收起进入，展开概况后仍可触摸输入', async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('knowloop:guest:overview', 'true'))
      await page.goto('/')
      const input = page.getByRole('textbox', { name: '提问' })
      await expect(input).toBeEditable()
      const toggle = page.getByRole('button', { name: '展开资料概况' })
      await expect(toggle).toHaveAttribute('aria-expanded', 'false')
      await toggle.tap()
      await expect(page.getByRole('button', { name: '收起资料概况' })).toHaveAttribute('aria-expanded', 'true')
      await input.tap()
      await expect(input).toBeFocused()
      await page.keyboard.type('HT-042')
      await expect(input).toHaveValue('HT-042')
      await page.keyboard.press('Escape')
      await expect(toggle).toHaveAttribute('aria-expanded', 'false')
      await expect(input).toBeFocused()
    })

    test('模型选择、发送和停止的触控区域分开', async ({ page }) => {
      await page.goto(`/chat/${conversation.id}`)
      const model = page.getByRole('combobox', { name: '聊天模型' })
      const send = page.getByRole('button', { name: '发送', exact: true })
      await page.getByRole('textbox', { name: '提问' }).fill('验收问题')
      await expect(send).toBeEnabled()
      await expectSeparateControls(model, send)
      await model.tap()
      await expect(page.getByRole('option', { name: /演示连接/ })).toBeVisible()
      await page.keyboard.press('Escape')
      await Promise.all([page.waitForRequest('**/api/v1/chat/stream'), send.tap()])
      const stop = page.getByRole('button', { name: '停止生成' })
      await expect(stop).toBeVisible()
      await expectSeparateControls(model, stop)
      await Promise.all([page.waitForRequest('**/turns/turn-1/cancel'), stop.tap()])
    })
  })
}

test('桌面首页遵循原有的资料概况展开偏好', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: '收起资料概况' })).toBeVisible()
  await page.getByRole('textbox', { name: '提问' }).fill('桌面验收')
  await expectSeparateControls(page.getByRole('combobox', { name: '聊天模型' }), page.getByRole('button', { name: '发送', exact: true }))
})

test('学习标签支持方向键并只占一个 Tab 停靠点', async ({ page }) => {
  await page.goto('/learning')
  const practice = page.getByRole('tab', { name: '练习', exact: true })
  const flashcards = page.getByRole('tab', { name: '闪卡复习' })
  await practice.focus()
  await page.keyboard.press('ArrowRight')
  await expect(flashcards).toBeFocused()
  await expect(flashcards).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tabpanel', { name: '闪卡复习' })).toBeVisible()
  expect(await page.getByRole('tab').evaluateAll((tabs) => tabs.filter((tab) => tab.tabIndex === 0).length)).toBe(1)
  await page.keyboard.press('Home')
  await expect(practice).toBeFocused()
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByRole('tab', { name: /错题本/ })).toBeFocused()
  await page.keyboard.press('ArrowRight')
  await expect(practice).toBeFocused()
})

test('可以从简答框用键盘打开手写答案文件选择器', async ({ page }) => {
  await page.goto('/learning')
  await page.getByRole('button', { name: /继续上次练习/ }).click()
  await page.getByRole('textbox', { name: '在线文字作答' }).fill('已有草稿')
  await page.keyboard.press('Tab')
  const upload = page.getByRole('button', { name: '拍照 / 上传手写图片' })
  await expect(upload).toBeFocused()
  for (const key of ['Enter', 'Space']) {
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.keyboard.press(key)])
    expect(await chooser.element().getAttribute('accept')).toBe('image/png,image/jpeg')
  }
})
