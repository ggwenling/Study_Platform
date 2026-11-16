#!/usr/bin/env node

import { readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';

const HELP = `Usage: node scripts/audit-build-output.mjs <build-directory> [--top <count>]

Read a completed frontend build directory and report asset sizes, source maps,
precompressed files, and hash-like asset names. This script never changes files.
It cannot verify CDN/server cache headers, response compression, or whether every
emitted asset is reachable from a real user journey.`;

const SKIP_DIRECTORIES = new Set(['.git', 'node_modules']);
const TEXT_EXTENSIONS = new Set(['.css', '.js', '.mjs', '.cjs']);
const MAP_EXTENSION = '.map';
const PRECOMPRESSED_EXTENSIONS = new Set(['.br', '.gz']);

function parseArgs(argv) {
  if (argv.includes('--help') || argv.includes('-h')) return { help: true };
  const positional = [];
  let top = 20;
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--top') {
      const count = argv[index + 1];
      if (!/^\d+$/.test(count) || Number(count) < 1) throw new Error('--top must be a positive integer.');
      top = Number(count);
      index += 1;
    } else if (value.startsWith('--')) {
      throw new Error(`Unknown option: ${value}`);
    } else {
      positional.push(value);
    }
  }
  if (positional.length !== 1) throw new Error('Provide exactly one build directory.');
  return { directory: path.resolve(positional[0]), top };
}

async function walk(directory, root, files) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (!SKIP_DIRECTORIES.has(entry.name)) await walk(path.join(directory, entry.name), root, files);
    } else if (entry.isFile()) {
      const absolute = path.join(directory, entry.name);
      const details = await stat(absolute);
      files.push({
        absolute,
        relative: path.relative(root, absolute),
        extension: path.extname(entry.name).toLowerCase(),
        size: details.size,
      });
    }
  }
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 ** 2).toFixed(2)} MiB`;
}

function appearsFingerprinted(relativePath) {
  const base = path.basename(relativePath);
  return /(?:^|[._-])[A-Za-z0-9_-]{8,}(?=\.[^.]+$)/.test(base);
}

function printList(items, empty) {
  if (!items.length) console.log(`- ${empty}`);
  else items.forEach((item) => console.log(`- ${item}`));
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

  let directoryStats;
  try {
    directoryStats = await stat(options.directory);
  } catch {
    console.error(`Error: build directory does not exist: ${options.directory}`);
    process.exitCode = 1;
    return;
  }
  if (!directoryStats.isDirectory()) {
    console.error(`Error: expected a directory: ${options.directory}`);
    process.exitCode = 1;
    return;
  }

  const files = [];
  await walk(options.directory, options.directory, files);
  const payloadFiles = files.filter((file) => !PRECOMPRESSED_EXTENSIONS.has(file.extension));
  const sourceMaps = files.filter((file) => file.extension === MAP_EXTENSION);
  const precompressed = files.filter((file) => PRECOMPRESSED_EXTENSIONS.has(file.extension));
  const likelyStatic = payloadFiles.filter((file) => !['.html', MAP_EXTENSION].includes(file.extension));
  const fingerprinted = likelyStatic.filter((file) => appearsFingerprinted(file.relative));
  const textFiles = payloadFiles.filter((file) => TEXT_EXTENSIONS.has(file.extension));
  const inlineMaps = [];
  const mapReferences = [];

  for (const file of textFiles) {
    if (file.size > 5 * 1024 * 1024) continue;
    const content = await readFile(file.absolute, 'utf8');
    if (/sourceMappingURL=data:/i.test(content)) inlineMaps.push(file.relative);
    if (/sourceMappingURL=(?!data:)[^\s]+/i.test(content)) mapReferences.push(file.relative);
  }

  console.log('# Build output audit');
  console.log(`\nDirectory: ${options.directory}`);
  console.log(`Files: ${files.length}; payload size excluding .br/.gz: ${formatBytes(payloadFiles.reduce((sum, file) => sum + file.size, 0))}`);

  console.log('\n## Largest emitted files');
  printList(
    [...payloadFiles].sort((a, b) => b.size - a.size).slice(0, options.top)
      .map((file) => `${file.relative}: ${formatBytes(file.size)}`),
    'No files found',
  );

  console.log('\n## Cacheability signals from filenames');
  console.log(`- Hash-like names: ${fingerprinted.length}/${likelyStatic.length} non-HTML, non-map files`);
  if (likelyStatic.length && fingerprinted.length < likelyStatic.length) {
    const examples = likelyStatic.filter((file) => !appearsFingerprinted(file.relative)).slice(0, 10);
    printList(examples.map((file) => `No obvious fingerprint: ${file.relative}`), 'All likely static assets appear fingerprinted');
  }

  console.log('\n## Source maps and precompression');
  console.log(`- Separate .map files: ${sourceMaps.length} (${formatBytes(sourceMaps.reduce((sum, file) => sum + file.size, 0))})`);
  console.log(`- Inline source-map references: ${inlineMaps.length}`);
  console.log(`- External source-map references: ${mapReferences.length}`);
  console.log(`- Precompressed .br/.gz files: ${precompressed.length}`);

  console.log('\n## What this report cannot prove');
  console.log('- A hash-like filename does not prove Cache-Control, CDN cache keys, service-worker updates, or deployment atomicity are correct.');
  console.log('- A .br or .gz file does not prove the server returns it with Content-Encoding.');
  console.log('- A .map file may be intentionally retained for error reporting; verify its upload and public-serving policy before deleting it.');
  console.log('- Inspect actual production response headers and a user journey before declaring cache or load improvements.');
}

main().catch((error) => {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
});
