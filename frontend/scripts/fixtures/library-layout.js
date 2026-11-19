// Extends the in-browser settings fixture with two libraries and a populated activity chart.
(() => {
  const upstream = window.fetch;
  const secondId = '33333333-3333-4333-8333-333333333333';
  const activity = Array.from({ length: 84 }, (_, day) => ({
    date: new Date(Date.UTC(2026, 8, 1 + day)).toISOString().slice(0, 10),
    count: day % 9 === 0 ? day % 5 : 0,
  }));
  window.fetch = async (input, options) => {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (path === '/api/v1/knowledge-bases') {
      const { items: [first] } = await (await upstream(input, options)).json();
      return Response.json({ items: [first, { ...first, id: secondId, name: '【模拟】第二个知识库' }], next_cursor: null });
    }
    const library = path.match(/^\/api\/v1\/knowledge-bases\/([^/]+)\//)?.[1];
    if (!library) return upstream(input, options);
    if (path.endsWith('/overview')) return Response.json({
      file_count: 1, ready_count: 1, parsed_count: 1, fragment_count: 0, cut_count: 1, embedded_count: 1,
      ocr_count: 0, ocr_queue: 0, ocr_material_ids: [], processing_count: 0, failed_count: 0,
      last_synced_at: null, topics: [], tags: [], activity,
    });
    if (path.endsWith('/fragments')) return Response.json({ items: [], total: 0, page_size: 20 });
    if (path.endsWith('/notes')) return Response.json({ items: [], next_cursor: null });
    if (path.endsWith('/materials')) {
      const second = library === secondId;
      if (second) await new Promise((resolve) => setTimeout(resolve, 450));
      const items = Array.from({ length: second ? 2 : 1 }, (_, index) => ({
        id: `${library}-${index}`, knowledge_base_id: library, title: `模拟资料 ${index + 1}`, status: 'ready',
      }));
      return Response.json({ items, next_cursor: null });
    }
    return upstream(input, options);
  };
})();
