#!/usr/bin/env node

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { analyzeArtifact } from './parse-performance-artifact.mjs';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const fixtureDirectory = path.join(scriptDirectory, '..', 'evals', 'fixtures');

async function load(name) {
  return JSON.parse(await readFile(path.join(fixtureDirectory, name), 'utf8'));
}

const har = analyzeArtifact(await load('network-sample.har.json'));
assert.equal(har.format, 'har');
assert.equal(har.summary.requests, 3);
assert.equal(har.redirects[0].to, 'https://www.example.test/cn/');
assert.equal(har.slowRequests[0].url, 'https://img.example-cdn.test/products/hero.avif');
assert.equal(har.slowRequests[0].timings.wait, 1150);
assert.equal(har.slowRequests[1].timings.dns, null);

const trace = analyzeArtifact(await load('runtime-sample.trace.json'));
assert.equal(trace.format, 'trace');
assert.equal(trace.summary.longEventCandidates, 2);
assert.equal(trace.redirects[0].from, 'http://shop.example.test/');
assert.equal(trace.longEventCandidates[0].name, 'EventDispatch');

console.log('Performance artifact fixtures passed.');
