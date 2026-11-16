#!/usr/bin/env node

import { spawn } from 'node:child_process';

const HELP = `Usage: node scripts/probe-url-timing.mjs <http-or-https-url> [options]

Options:
  --follow              Follow redirects (off by default so the first hop is visible).
  --max-redirs <count>  Maximum redirects when --follow is set (default: 10).
  --help                Show this help.

Runs one unauthenticated GET with curl and discards the response body. Use it only
with URLs that are safe to read: it sends no body, cookies, or credentials, but a
server can define side effects for GET. It reports redirect status/location plus
DNS, TCP, TLS, first-byte, and total timings from this machine. It is one local
network sample, not a measurement of real-user latency or CDN behavior in every region.`;

const MARKER = '__NETWORK_METRICS__';
const WRITE_OUT = `\n${MARKER}\nhttp_code=%{http_code}\nhttp_version=%{http_version}\nurl_effective=%{url_effective}\nremote_ip=%{remote_ip}\nnum_connects=%{num_connects}\nnum_redirects=%{num_redirects}\ntime_namelookup=%{time_namelookup}\ntime_connect=%{time_connect}\ntime_appconnect=%{time_appconnect}\ntime_starttransfer=%{time_starttransfer}\ntime_total=%{time_total}\ntime_redirect=%{time_redirect}\nsize_download=%{size_download}\ncontent_type=%{content_type}\n`;

function parseArgs(argv) {
  if (argv.includes('--help') || argv.includes('-h')) return { help: true };
  const options = { follow: false, maxRedirs: 10 };
  const positional = [];
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--follow') {
      options.follow = true;
    } else if (value === '--max-redirs') {
      const count = argv[index + 1];
      if (!/^\d+$/.test(count) || Number(count) < 0) throw new Error('--max-redirs must be a non-negative integer.');
      options.maxRedirs = Number(count);
      index += 1;
    } else if (value.startsWith('--')) {
      throw new Error(`Unknown option: ${value}`);
    } else {
      positional.push(value);
    }
  }
  if (positional.length !== 1) throw new Error('Provide exactly one URL.');
  const url = new URL(positional[0]);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('URL must use http or https.');
  if (url.username || url.password) throw new Error('URLs with embedded credentials are not allowed.');
  options.url = url.toString();
  return options;
}

function runCurl(args) {
  return new Promise((resolve, reject) => {
    const child = spawn('curl', args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.once('error', (error) => reject(error));
    child.once('close', (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`curl exited with code ${code}${stderr ? `: ${stderr.trim()}` : ''}`));
    });
  });
}

function parseMetrics(text) {
  const result = {};
  for (const line of text.trim().split(/\r?\n/)) {
    const separator = line.indexOf('=');
    if (separator > 0) result[line.slice(0, separator)] = line.slice(separator + 1);
  }
  return result;
}

function parseHeaderBlocks(text) {
  return text.split(/\r?\n\r?\n/).map((block) => {
    const lines = block.split(/\r?\n/).filter(Boolean);
    const status = lines.find((line) => /^HTTP\/\d(?:\.\d)?\s+\d{3}/i.test(line));
    if (!status) return null;
    const fields = new Map();
    for (const line of lines.slice(1)) {
      const separator = line.indexOf(':');
      if (separator > 0) fields.set(line.slice(0, separator).toLowerCase(), line.slice(separator + 1).trim());
    }
    return {
      status,
      location: fields.get('location'),
      hsts: fields.get('strict-transport-security'),
      altSvc: fields.get('alt-svc'),
      serverTiming: fields.get('server-timing'),
      cacheControl: fields.get('cache-control'),
    };
  }).filter(Boolean);
}

function milliseconds(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? `${(number * 1000).toFixed(1)} ms` : 'n/a';
}

function difference(later, earlier) {
  const end = Number(later);
  const start = Number(earlier);
  return Number.isFinite(end) && Number.isFinite(start) && end >= start
    ? milliseconds(end - start)
    : 'n/a';
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(`Error: ${error.message}\n\n${HELP}`);
    process.exitCode = 1;
    return;
  }
  if (options.help) {
    console.log(HELP);
    return;
  }

  const args = [
    '--silent', '--show-error', '--get', '--dump-header', '-', '--output', '/dev/null',
    '--connect-timeout', '10', '--max-time', '30', '--proto', '=http,https',
    '--proto-redir', '=http,https', '--write-out', WRITE_OUT,
  ];
  if (options.follow) args.push('--location', '--max-redirs', String(options.maxRedirs));
  args.push(options.url);

  let output;
  try {
    output = await runCurl(args);
  } catch (error) {
    console.error(`Error: ${error.message}`);
    process.exitCode = 1;
    return;
  }
  const markerIndex = output.lastIndexOf(MARKER);
  if (markerIndex === -1) {
    console.error('Error: curl did not return timing metrics. Upgrade curl and retry.');
    process.exitCode = 1;
    return;
  }
  const headers = output.slice(0, markerIndex);
  const metrics = parseMetrics(output.slice(markerIndex + MARKER.length));
  const blocks = parseHeaderBlocks(headers);
  const connectionEnd = Number(metrics.time_appconnect) > 0 ? metrics.time_appconnect : metrics.time_connect;

  console.log('# URL timing probe');
  console.log(`\nInput URL: ${options.url}`);
  console.log(`Effective URL: ${metrics.url_effective || 'n/a'}`);
  console.log(`HTTP: ${metrics.http_version || 'n/a'}; status: ${metrics.http_code || 'n/a'}; remote IP: ${metrics.remote_ip || 'n/a'}`);
  console.log(`New connections: ${metrics.num_connects || 'n/a'}; followed redirects: ${metrics.num_redirects || '0'}`);

  console.log('\n## Observed response hops');
  if (!blocks.length) console.log('- No parseable HTTP response headers were returned.');
  for (const [index, block] of blocks.entries()) {
    console.log(`- Hop ${index + 1}: ${block.status}`);
    if (block.location) console.log(`  Location: ${block.location}`);
    if (block.hsts) console.log(`  Strict-Transport-Security: ${block.hsts}`);
    if (block.altSvc) console.log(`  Alt-Svc: ${block.altSvc}`);
    if (block.serverTiming) console.log(`  Server-Timing: ${block.serverTiming}`);
    if (block.cacheControl) console.log(`  Cache-Control: ${block.cacheControl}`);
  }

  console.log('\n## Timing from this probe');
  console.log(`- DNS lookup: ${milliseconds(metrics.time_namelookup)}`);
  console.log(`- TCP after DNS: ${difference(metrics.time_connect, metrics.time_namelookup)}`);
  console.log(`- TLS after TCP: ${Number(metrics.time_appconnect) > 0 ? difference(metrics.time_appconnect, metrics.time_connect) : 'n/a (not an HTTPS handshake)'}`);
  console.log(`- Post-connect to first byte: ${difference(metrics.time_starttransfer, connectionEnd)}`);
  console.log(`- Redirect time: ${milliseconds(metrics.time_redirect)}`);
  console.log(`- Total: ${milliseconds(metrics.time_total)}; response bytes: ${metrics.size_download || 'n/a'}; type: ${metrics.content_type || 'n/a'}`);

  console.log('\n## Interpretation boundary');
  console.log('- These values are one curl connection from this machine; DNS cache, proxy/VPN, connection reuse, CDN edge, IPv4/IPv6 routing, and browser scheduling can differ for users.');
  console.log('- Use a cold-cache Chrome trace/HAR and field or regional evidence before treating DNS, TLS, redirect, or TTFB as a production finding.');
  console.log('- This probe intentionally omits response bodies and sensitive headers such as Set-Cookie from its output.');
}

main();
