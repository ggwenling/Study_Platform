/**
 * Local OpenAI-compatible stub for the isolated UX journey.
 * Embeddings are a constant non-zero 1024-d vector. Chat returns a fixed
 * citation sentence when the prompt carries retrieved evidence. This is not
 * a real model and must not be reported as answer-quality evidence.
 */
import http from 'node:http'
import fs from 'node:fs'

const port = Number(process.env.UX_MOCK_PORT || 18080)
const logPath = process.env.UX_MOCK_LOG || ''
const vector = Array.from({ length: 1024 }, () => 0.2)
const stats = {
  embeddings: 0,
  chat: 0,
  chatWithEvidence: 0,
  other: 0,
  startedAt: new Date().toISOString(),
}

function log(line) {
  const text = `${new Date().toISOString()} ${line}\n`
  if (logPath) fs.appendFileSync(logPath, text)
}

function messageText(message) {
  const content = message?.content
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content.map((part) => (typeof part === 'string' ? part : part?.text || part?.content || '')).join('')
  }
  return ''
}

function chatContent(body) {
  const blob = (body.messages || []).map(messageText).join('\n')
  if (blob.includes('本轮资料JSON')) {
    stats.chatWithEvidence += 1
    return '概念说明 [S1]'
  }
  if (blob.includes('只返回 JSON 对象，键为 topic')) {
    return '{"topic":"虚构矿物","tags":["青柠晶体","验收夹具"]}'
  }
  return 'OK'
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload)
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(body),
  })
  res.end(body)
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on('data', (chunk) => chunks.push(chunk))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === 'GET' && (req.url === '/health' || req.url === '/__stats')) {
      sendJson(res, 200, req.url === '/health' ? { status: 'ok' } : stats)
      return
    }
    if (req.method !== 'POST') {
      sendJson(res, 404, { error: { message: 'not found' } })
      return
    }
    const raw = await readBody(req)
    const body = raw ? JSON.parse(raw) : {}
    const path = req.url || ''
    if (path.endsWith('/embeddings')) {
      stats.embeddings += 1
      const inputs = Array.isArray(body.input) ? body.input : [body.input]
      log(`embeddings n=${inputs.length} model=${body.model || ''}`)
      sendJson(res, 200, {
        object: 'list',
        model: body.model || 'ux-embed',
        data: inputs.map((_, index) => ({ object: 'embedding', index, embedding: vector })),
        usage: { prompt_tokens: inputs.length, total_tokens: inputs.length },
      })
      return
    }
    if (path.endsWith('/chat/completions')) {
      stats.chat += 1
      const content = chatContent(body)
      log(`chat stream=${Boolean(body.stream)} evidence=${content.includes('[S1]')} model=${body.model || ''}`)
      if (body.stream) {
        const part = {
          id: 'ux-fixture',
          object: 'chat.completion.chunk',
          created: 1,
          model: body.model || 'ux-chat',
          choices: [{ index: 0, delta: { role: 'assistant', content }, finish_reason: null }],
        }
        const payload = `data: ${JSON.stringify(part)}\n\ndata: [DONE]\n\n`
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Content-Length': Buffer.byteLength(payload),
        })
        res.end(payload)
        return
      }
      sendJson(res, 200, {
        id: 'ux-fixture',
        object: 'chat.completion',
        created: 1,
        model: body.model || 'ux-chat',
        choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 2, completion_tokens: 2, total_tokens: 4 },
      })
      return
    }
    stats.other += 1
    log(`other ${req.method} ${path}`)
    sendJson(res, 404, { error: { message: 'unsupported path' } })
  } catch (error) {
    log(`error ${error instanceof Error ? error.message : String(error)}`)
    if (!res.headersSent) sendJson(res, 400, { error: { message: 'bad request' } })
  }
})

server.listen(port, '127.0.0.1', () => {
  log(`listening http://127.0.0.1:${port}/v1`)
  console.log(`ux-mock listening on http://127.0.0.1:${port}/v1`)
})
