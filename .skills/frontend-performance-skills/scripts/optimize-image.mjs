#!/usr/bin/env node

import { access, stat } from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

const HELP = `Usage:
  node scripts/optimize-image.mjs <input-file> [options]

Options:
  --out <file>                   Output path. Defaults to <name>.optimized.<format>.
  --format <jpg|png|webp|avif>   Output format. Defaults to the input format.
  --width <px>                   Maximum width; never enlarges the image.
  --height <px>                  Maximum height; never enlarges the image.
  --quality <1-100>              Encoder quality (default: 80 for lossy output).
  --keep-metadata                Preserve EXIF and color-profile metadata.
  --overwrite                    Permit replacing an existing --out file.
  --dry-run                      Print the ImageMagick command without modifying files.
  --help                         Show this help.

Requirements:
  ImageMagick 7 (the \`magick\` command) with a delegate for the requested format.

The command reads one image and writes one new file. It never changes the input file.
It applies EXIF orientation and strips metadata. Review visual quality and dimensions
at every rendered breakpoint before replacing a production asset.`;

const FORMAT_ALIASES = new Map([
  ['jpg', 'jpg'],
  ['jpeg', 'jpg'],
  ['png', 'png'],
  ['webp', 'webp'],
  ['avif', 'avif'],
]);

function fail(message) {
  console.error(`Error: ${message}`);
  process.exitCode = 1;
}

function parsePositiveInteger(value, name) {
  if (!/^\d+$/.test(value) || Number(value) < 1) {
    throw new Error(`${name} must be a positive integer.`);
  }
  return Number(value);
}

function parseArgs(argv) {
  const options = { dryRun: false, overwrite: false, keepMetadata: false };
  const positional = [];
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === '--help' || token === '-h') return { help: true };
    if (token === '--dry-run') {
      options.dryRun = true;
      continue;
    }
    if (token === '--overwrite') {
      options.overwrite = true;
      continue;
    }
    if (token === '--keep-metadata') {
      options.keepMetadata = true;
      continue;
    }
    if (['--out', '--format', '--width', '--height', '--quality'].includes(token)) {
      const value = argv[index + 1];
      if (!value || value.startsWith('--')) throw new Error(`${token} requires a value.`);
      options[token.slice(2)] = value;
      index += 1;
      continue;
    }
    if (token.startsWith('--')) throw new Error(`Unknown option: ${token}`);
    positional.push(token);
  }
  if (positional.length !== 1) throw new Error('Provide exactly one input image file.');
  options.input = positional[0];
  return options;
}

function run(command, args, { capture = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit' });
    let output = '';
    if (capture) {
      child.stdout.on('data', (chunk) => { output += chunk; });
      child.stderr.on('data', (chunk) => { output += chunk; });
    }
    child.once('error', (error) => reject(error));
    child.once('close', (code) => {
      if (code === 0) resolve(output);
      else reject(new Error(`${command} exited with code ${code}${output ? `: ${output.trim()}` : ''}`));
    });
  });
}

async function exists(file) {
  try {
    await access(file, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    fail(error.message);
    console.error(`\n${HELP}`);
    return;
  }
  if (options.help) {
    console.log(HELP);
    return;
  }

  const input = path.resolve(options.input);
  if (!(await exists(input))) {
    fail(`Input does not exist: ${input}`);
    return;
  }
  if (!(await stat(input)).isFile()) {
    fail(`Input must be a file: ${input}`);
    return;
  }

  const inputExtension = path.extname(input).slice(1).toLowerCase();
  const format = FORMAT_ALIASES.get((options.format ?? inputExtension).toLowerCase());
  if (!format) {
    fail('Use --format with one of: jpg, png, webp, avif.');
    return;
  }
  let width;
  let height;
  let quality;
  try {
    width = options.width && parsePositiveInteger(options.width, '--width');
    height = options.height && parsePositiveInteger(options.height, '--height');
    quality = options.quality && parsePositiveInteger(options.quality, '--quality');
    if (quality && quality > 100) throw new Error('--quality must be between 1 and 100.');
  } catch (error) {
    fail(error.message);
    return;
  }

  const output = path.resolve(options.out ?? path.join(
    path.dirname(input),
    `${path.basename(input, path.extname(input))}.optimized.${format}`,
  ));
  if (input === output) {
    fail('Output must be different from the input file.');
    return;
  }
  const outputExtension = path.extname(output).slice(1).toLowerCase();
  if (outputExtension && FORMAT_ALIASES.get(outputExtension) !== format) {
    fail(`Output extension .${outputExtension} does not match the ${format.toUpperCase()} format.`);
    return;
  }
  if ((await exists(output)) && !options.overwrite) {
    fail(`Output exists: ${output}. Choose another --out path or add --overwrite.`);
    return;
  }

  try {
    await run('magick', ['-version'], { capture: true });
  } catch {
    fail('ImageMagick 7 was not found. Install it separately, then rerun this command.');
    return;
  }

  const resize = width || height
    ? `${width ?? ''}x${height ?? ''}>`
    : undefined;
  const outputSpecifier = `${format}:${output}`;
  const command = [input, '-auto-orient'];
  if (!options.keepMetadata) command.push('-strip');
  if (resize) command.push('-resize', resize);
  if (quality) command.push('-quality', String(quality));
  else if (format === 'jpg' || format === 'webp' || format === 'avif') command.push('-quality', '80');
  command.push(outputSpecifier);

  console.log(`Input:  ${input}`);
  console.log(`Output: ${output}`);
  console.log(`Command: magick ${command.map((part) => JSON.stringify(part)).join(' ')}`);
  if (options.dryRun) {
    console.log('Dry run: no files were written.');
    return;
  }

  const before = (await stat(input)).size;
  try {
    await run('magick', command);
  } catch (error) {
    fail(`Conversion failed. Confirm that ImageMagick supports ${format.toUpperCase()} on this machine. ${error.message}`);
    return;
  }
  const after = (await stat(output)).size;
  const delta = before === 0 ? 0 : ((after - before) / before) * 100;
  console.log(`Wrote ${output}`);
  console.log(`Bytes: ${before} -> ${after} (${delta.toFixed(1)}%)`);
}

main();
