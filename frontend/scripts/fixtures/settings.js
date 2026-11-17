// Isolated browser fixtures: API operations below only change this in-memory preview.
(() => {
  const date = '2026-11-17T10:00:00Z';
  const library = { id: '11111111-1111-4111-8111-111111111111', name: '项目资料库', description: '设置页面回归资料', created_at: date, updated_at: date };
  const bucket = (key, calls) => ({ key, calls, successes: calls, failures: 0, fallback_calls: 0, total_tokens: calls * 100 });
  const base = { kind: 'llm', base_url: 'https://models.example.test/v1', model: 'example-chat-model', dimensions: null, key_configured: true,
    key_suffix: 'demo', revision: 1, status: 'ready', is_default: false, tested_at: date, error_code: null, libraries: [] };
  const preview = window.settingsPreview = {
    encryption: true, usageError: false,
    connections: [
      { ...base, id: 'backup', name: '备用文本连接' },
      { ...base, id: 'draft', name: '待配置文本连接', status: 'draft', tested_at: null },
      { ...base, id: 'current', name: '当前文本连接', is_default: true, status: 'failed', error_code: 'model_auth_failed' },
      { ...base, id: 'embedding', kind: 'embedding', name: '资料向量连接', model: 'example-embedding-model', dimensions: 1024, is_default: true, libraries: [{ id: library.id, name: library.name }] },
    ],
    usage: { days: 30, since: date, totals: { calls: 10, successes: 8, failures: 2, fallback_calls: 1, prompt_tokens: 600, completion_tokens: 400, total_tokens: 1000 },
      by_day: [], by_purpose: [bucket('chat', 6), bucket('embedding', 4)], by_model: [bucket('example-chat-model', 10)],
      by_connection: [{ ...bucket('platform', 10), label: '平台连接' }] },
  };
  const originalFetch = window.fetch;
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/v1/')) return originalFetch(input, options);
    const path = url.pathname.slice('/api/v1'.length);
    const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : {};
    const reply = (data, status = 200) => new Response(status === 204 ? null : JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
    if (path.startsWith('/auth/')) return reply(path.endsWith('/csrf') ? { csrf_token: 'preview' } : {
      user: { id: 'settings-preview', display_name: '设置预览用户', email: 'settings@example.test', avatar_url: null }, access_token: 'preview', csrf_token: 'preview',
    });
    if (path === '/me/model-usage') return preview.usageError ? reply({ code: 'preview_unavailable', message: '暂时无法加载用量', retryable: true }, 503) : reply(preview.usage);
    if (path === '/me/model-connections' && method === 'GET') return reply({ items: preview.connections, encryption_available: preview.encryption, platform_llm: 'platform-chat', platform_embedding: 'platform-embedding' });
    if (path.startsWith('/me/model-connections/defaults/') && method === 'PUT') {
      const kind = path.split('/').at(-1);
      preview.connections.forEach(row => { if (row.kind === kind) row.is_default = row.id === body.connection_id; });
      return reply(null, 204);
    }
    if (path.startsWith('/me/model-connections/')) {
      const row = preview.connections.find(item => path.split('/')[3] === item.id);
      if (row && path.endsWith('/test') && method === 'POST') { Object.assign(row, { status: 'ready', error_code: null, tested_at: date }); return reply(row); }
      if (row && method === 'PATCH') { Object.assign(row, { ...body, status: 'draft' }); return reply(row); }
      if (row && method === 'DELETE' && !row.is_default && !row.libraries.length) { preview.connections = preview.connections.filter(item => item !== row); return reply(null, 204); }
    }
    if (method !== 'GET') return reply({ code: 'preview_blocked', message: '预览不支持该操作', retryable: false }, 405);
    if (path === '/knowledge-bases') return reply({ items: [library], next_cursor: null });
    if (path === '/conversations') return reply({ items: Array.from({ length: 8 }, (_, index) => ({
      id: `22222222-2222-4222-8222-${String(index).padStart(12, '0')}`, title: `项目讨论 ${index + 1}`, project_knowledge_base_id: library.id, created_at: date, updated_at: date,
    })), next_cursor: null });
    if (path === '/dashboard/overview') return reply({ knowledge_base_count: 1, material_count: 0, conversation_count: 8, completed_qa_count: 0, activity: [] });
    if (path === '/capabilities') return reply({ rag: true, ocr: false, answer_modes: ['knowledge_only'], upload_mime_types: [], max_upload_bytes: 1000 });
    if (path.endsWith('/materials')) return reply({ items: [], next_cursor: null });
    return reply({ code: 'preview_not_found', message: '预览没有此资源', retryable: false }, 404);
  };
})();
