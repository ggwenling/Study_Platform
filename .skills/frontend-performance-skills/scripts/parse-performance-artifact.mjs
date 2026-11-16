#!/usr/bin/env node

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const HELP = `Usage: node scripts/parse-performance-artifact.mjs <trace-or-har.json> [--json]

Read a Chrome-style JSON trace or HAR and print a small evidence summary. The
parser never modifies the artifact. It identifies long-duration trace-event
candidates, origins, redirect responses, and slow HAR requests; it cannot prove
browser scheduling, LCP attribution, cache state, or source-level causality.`;

function getOrigin(url) {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

function number(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function timingValue(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function harTimings(entry) {
  const timings = entry.timings || {};
  return {
    blocked: timingValue(timings.blocked),
    dns: timingValue(timings.dns),
    connect: timingValue(timings.connect),
    ssl: timingValue(timings.ssl),
    send: timingValue(timings.send),
    wait: timingValue(timings.wait),
    receive: timingValue(timings.receive),
  };
}

function headerValue(headers = [], name) {
  const found = headers.find((header) => header.name?.toLowerCase() === name);
  return found?.value;
}

function resourceType(entry) {
  const explicit = entry._resourceType || entry._resourceTypeName;
  if (explicit) return String(explicit);
  const mime = entry.response?.content?.mimeType || '';
  if (mime.includes('javascript')) return 'script';
  if (mime.includes('css')) return 'stylesheet';
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('font/') || mime.includes('woff')) return 'font';
  if (mime.includes('html')) return 'document';
  return 'other';
}

function summarizeOrigins(records) {
  const origins = new Map();
  for (const record of records) {
    const origin = getOrigin(record.url);
    if (!origin) continue;
    const current = origins.get(origin) || { origin, requests: 0, transferBytes: 0, totalTimeMs: 0, redirects: 0 };
    current.requests += 1;
    current.transferBytes += number(record.transferBytes);
    current.totalTimeMs += number(record.timeMs);
    current.redirects += record.redirect ? 1 : 0;
    origins.set(origin, current);
  }
  return [...origins.values()].sort((a, b) => b.totalTimeMs - a.totalTimeMs || b.transferBytes - a.transferBytes);
}

export function analyzeHar(har) {
  const entries = har?.log?.entries;
  if (!Array.isArray(entries)) throw new Error('HAR must contain log.entries.');
  const records = entries.map((entry) => ({
    url: entry.request?.url || '',
    method: entry.request?.method || '',
    status: number(entry.response?.status),
    timeMs: number(entry.time),
    transferBytes: number(entry._transferSize || entry.response?.bodySize || entry.response?.content?.size),
    type: resourceType(entry),
    timings: harTimings(entry),
    redirect: number(entry.response?.status) >= 300 && number(entry.response?.status) < 400,
    location: headerValue(entry.response?.headers, 'location'),
  }));
  const redirects = records.filter((record) => record.redirect).map((record) => ({
    status: record.status,
    from: record.url,
    to: record.location || null,
  }));
  const slowRequests = [...records].sort((a, b) => b.timeMs - a.timeMs).slice(0, 10);
  return {
    format: 'har',
    summary: { requests: records.length, redirects: redirects.length },
    origins: summarizeOrigins(records),
    redirects,
    slowRequests,
  };
}

function eventData(event) {
  return event?.args?.data || event?.args || {};
}

function requestId(data) {
  return data.requestId || data.request_id || data.identifier || null;
}

function urlFromData(data) {
  return data.url || data.requestUrl || data.redirectURL || data.redirectUrl || null;
}

function responseLocation(data) {
  const headers = data.responseHeaders || data.headers || {};
  if (Array.isArray(headers)) return headerValue(headers, 'location');
  return headers.location || headers.Location || null;
}

export function analyzeTrace(trace) {
  const events = Array.isArray(trace) ? trace : trace?.traceEvents;
  if (!Array.isArray(events)) throw new Error('Trace must be an array or contain traceEvents.');
  const requests = new Map();
  const records = [];
  const redirects = [];
  const longEvents = [];

  for (const event of events) {
    const data = eventData(event);
    const url = urlFromData(data);
    const id = requestId(data);
    if (url && id) requests.set(String(id), url);
    if (url) records.push({ url, timeMs: 0, transferBytes: 0, redirect: false });

    const status = number(data.statusCode || data.status || data.responseCode);
    if (status >= 300 && status < 400) {
      const from = requests.get(String(id)) || url || null;
      redirects.push({ status, from, to: responseLocation(data) || data.redirectURL || data.redirectUrl || null });
    }

    const durationMs = number(event.dur) / 1000;
    if (durationMs >= 50) {
      longEvents.push({
        name: event.name || '(unnamed event)',
        category: event.cat || '',
        startMs: number(event.ts) / 1000,
        durationMs,
        url: url || null,
      });
    }
  }

  return {
    format: 'trace',
    summary: { events: events.length, longEventCandidates: longEvents.length, redirects: redirects.length },
    origins: summarizeOrigins(records),
    redirects,
    longEventCandidates: longEvents.sort((a, b) => b.durationMs - a.durationMs).slice(0, 20),
  };
}

export function analyzeArtifact(artifact) {
  if (Array.isArray(artifact) || Array.isArray(artifact?.traceEvents)) return analyzeTrace(artifact);
  if (Array.isArray(artifact?.log?.entries)) return analyzeHar(artifact);
  throw new Error('Unsupported JSON: expected a HAR log.entries or a Chrome-style traceEvents array.');
}

export function formatReport(result) {
  const lines = [`# Performance artifact summary`, ``, `Format: ${result.format}`];
  lines.push(`Summary: ${Object.entries(result.summary).map(([key, value]) => `${key}=${value}`).join(', ')}`);
  lines.push('', '## Origins');
  if (!result.origins.length) lines.push('- No URL origins found in this artifact.');
  else result.origins.slice(0, 10).forEach((origin) => lines.push(`- ${origin.origin}: ${origin.requests} requests; ${origin.transferBytes} bytes; ${origin.totalTimeMs.toFixed(1)} ms aggregate request time; ${origin.redirects} redirects`));
  lines.push('', '## Redirects');
  if (!result.redirects.length) lines.push('- No redirect responses found.');
  else result.redirects.slice(0, 10).forEach((redirect) => lines.push(`- ${redirect.status}: ${redirect.from || '(unknown source)'} -> ${redirect.to || '(Location unavailable)'}`));
  if (result.format === 'har') {
    lines.push('', '## Slow HAR requests');
    result.slowRequests.forEach((request) => {
      const timing = Object.entries(request.timings)
        .filter(([, value]) => value !== null)
        .map(([phase, value]) => `${phase}=${value.toFixed(1)} ms`)
        .join('; ');
      lines.push(`- ${request.timeMs.toFixed(1)} ms; ${request.status}; ${request.type}; ${request.url}${timing ? `; ${timing}` : ''}`);
    });
  } else {
    lines.push('', '## Long-duration trace-event candidates');
    result.longEventCandidates.forEach((event) => lines.push(`- ${event.durationMs.toFixed(1)} ms at ${event.startMs.toFixed(1)} ms; ${event.name}; ${event.category || 'no category'}${event.url ? `; ${event.url}` : ''}`));
  }
  lines.push('', '## Boundary', '- These are extraction leads. Confirm user impact, initiator chains, connection/cache state, and call stacks in DevTools before reporting a root cause.');
  return lines.join('\n');
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    console.log(HELP);
    return;
  }
  const json = args.includes('--json');
  const paths = args.filter((arg) => arg !== '--json');
  if (paths.length !== 1) throw new Error(`Provide exactly one artifact path.\n\n${HELP}`);
  const artifactPath = path.resolve(paths[0]);
  const artifact = JSON.parse(await readFile(artifactPath, 'utf8'));
  const result = analyzeArtifact(artifact);
  console.log(json ? JSON.stringify(result, null, 2) : formatReport(result));
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(`Error: ${error.message}`);
    process.exitCode = 1;
  });
}
