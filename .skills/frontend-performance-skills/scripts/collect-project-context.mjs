#!/usr/bin/env node

import { promises as fs } from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const HELP = `Usage: node scripts/collect-project-context.mjs [project-directory]

Read a frontend project's package-manager signals, configuration files, package
scripts, dependencies, source-file mix, and largest source files. The script
never installs dependencies or changes the inspected project.`;

const args = process.argv.slice(2);
if (args.includes('--help') || args.includes('-h')) {
  console.log(HELP);
  process.exit(0);
}
if (args.length > 1 || args[0]?.startsWith('-')) {
  console.error(`Error: provide at most one project directory.\n\n${HELP}`);
  process.exit(1);
}

const projectRoot = path.resolve(args[0] || process.cwd());
const ignoredDirectories = new Set([
  '.git', '.next', '.nuxt', '.output', '.turbo', 'build', 'coverage', 'dist',
  'node_modules', 'out', 'storybook-static', 'target',
]);
const sourceExtensions = new Set([
  '.astro', '.css', '.html', '.js', '.jsx', '.less', '.mjs', '.scss', '.svelte',
  '.ts', '.tsx', '.vue',
]);
const configNames = new Set([
  'angular.json', 'astro.config.js', 'astro.config.mjs', 'next.config.js',
  'next.config.mjs', 'next.config.ts', 'nuxt.config.js', 'nuxt.config.ts',
  'package.json', 'postcss.config.js', 'remix.config.js', 'rollup.config.js',
  'rsbuild.config.ts', 'rspack.config.js', 'svelte.config.js', 'tsconfig.json',
  'vite.config.js', 'vite.config.mjs', 'vite.config.ts', 'webpack.config.js',
]);

async function readJson(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch {
    return null;
  }
}

async function walk(directory, state) {
  let entries;
  try {
    entries = await fs.readdir(directory, { withFileTypes: true });
  } catch {
    return;
  }

  for (const entry of entries) {
    if (entry.name.startsWith('.') && entry.name !== '.browserslistrc') continue;
    const absolutePath = path.join(directory, entry.name);
    const relativePath = path.relative(projectRoot, absolutePath);

    if (entry.isDirectory()) {
      if (!ignoredDirectories.has(entry.name)) await walk(absolutePath, state);
      continue;
    }

    if (!entry.isFile()) continue;
    if (configNames.has(entry.name) || entry.name === '.browserslistrc') {
      state.configFiles.push(relativePath);
    }

    const extension = path.extname(entry.name);
    if (!sourceExtensions.has(extension)) continue;

    try {
      const { size } = await fs.stat(absolutePath);
      state.sourceCounts.set(extension || '(none)', (state.sourceCounts.get(extension || '(none)') || 0) + 1);
      state.largeSourceFiles.push({ path: relativePath, size });
    } catch {
      // A file may disappear while a dev server is rewriting it. Skipping it is fine.
    }
  }
}

function printList(items, emptyText = 'None found') {
  if (!items.length) {
    console.log(`- ${emptyText}`);
    return;
  }
  for (const item of items) console.log(`- ${item}`);
}

const packageJson = await readJson(path.join(projectRoot, 'package.json'));
const state = { configFiles: [], sourceCounts: new Map(), largeSourceFiles: [] };
await walk(projectRoot, state);

state.configFiles.sort();
state.largeSourceFiles.sort((a, b) => b.size - a.size);

console.log('# Frontend project context');
console.log(`\nProject: ${projectRoot}`);

console.log('\n## Package manager signals');
const lockfiles = [
  ['bun.lock', 'Bun'], ['bun.lockb', 'Bun'], ['package-lock.json', 'npm'],
  ['pnpm-lock.yaml', 'pnpm'], ['yarn.lock', 'Yarn'],
];
const foundManagers = [];
for (const [lockfile, manager] of lockfiles) {
  try {
    await fs.access(path.join(projectRoot, lockfile));
    foundManagers.push(`${manager} (${lockfile})`);
  } catch {
    // No lockfile for this manager.
  }
}
printList(foundManagers, 'No lockfile found');

console.log('\n## Project configuration');
printList(state.configFiles);

console.log('\n## Package scripts');
const scripts = packageJson?.scripts ? Object.entries(packageJson.scripts) : [];
printList(scripts.map(([name, command]) => `${name}: ${command}`), 'No package scripts found');

console.log('\n## Runtime dependencies');
const dependencies = packageJson?.dependencies ? Object.keys(packageJson.dependencies).sort() : [];
printList(dependencies, 'No runtime dependencies found');

console.log('\n## Development dependencies');
const developmentDependencies = packageJson?.devDependencies
  ? Object.keys(packageJson.devDependencies).sort()
  : [];
printList(developmentDependencies, 'No development dependencies found');

console.log('\n## Source file mix');
const counts = [...state.sourceCounts.entries()].sort((a, b) => b[1] - a[1]);
printList(counts.map(([extension, count]) => `${extension}: ${count}`), 'No common frontend source files found');

console.log('\n## Largest source files');
printList(
  state.largeSourceFiles.slice(0, 20).map(({ path: filePath, size }) => `${filePath}: ${(size / 1024).toFixed(1)} KiB`),
  'No common frontend source files found',
);

console.log('\n## What this report does not prove');
console.log('- Large source files are investigation leads, not measured runtime bottlenecks.');
console.log('- Installed dependencies do not reveal what ships to a route. Inspect a production build or bundle report.');
console.log('- No browser, field, network, or build timing data was collected.');
