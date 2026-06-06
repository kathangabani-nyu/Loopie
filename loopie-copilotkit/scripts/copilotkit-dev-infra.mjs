#!/usr/bin/env node
// Starts the local CopilotKit Intelligence stack (docker-compose.yml) before
// `npm run dev` when a license token is configured. Written by
// `copilotkit init`; safe to delete if you manage the stack yourself.
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const TOKEN_RE = /^\s*(?:export\s+)?COPILOTKIT_LICENSE_TOKEN=(.*)$/m;

/**
 * Reads the CopilotKit license token from ./.env, stripping quotes.
 *
 * @returns The token value, or an empty string when unset.
 */
function readLicenseToken() {
  if (!existsSync('.env')) {
    return '';
  }
  const match = TOKEN_RE.exec(readFileSync('.env', 'utf8'));
  if (!match) {
    return '';
  }
  return match[1].trim().replace(/^['"]|['"]$/g, '');
}

const token = readLicenseToken();

if (token === '') {
  console.log(
    'CopilotKit threads are locked — run `copilotkit license` to provision a license and activate them.',
  );
  process.exit(0);
}

if (!existsSync('docker-compose.yml')) {
  console.error(
    'COPILOTKIT_LICENSE_TOKEN is set but docker-compose.yml is missing; cannot start the Intelligence stack.',
  );
  process.exit(1);
}

const result = spawnSync('docker', ['compose', 'up', '-d', '--wait'], {
  stdio: 'inherit',
});

if (result.error) {
  console.error(
    `Failed to start the Intelligence stack: ${result.error.message}. Is Docker installed and running?`,
  );
  process.exit(1);
}

if ((result.status ?? 1) === 0) {
  process.exit(0);
}

// `up --wait` returns non-zero whenever any container has stopped — including
// one-shot provisioning services that exited 0 by design. Inspect the actual
// container states and only fail when a long-running service is unhealthy or
// a one-shot exited non-zero.
const ps = spawnSync(
  'docker',
  ['compose', 'ps', '--all', '--format', 'json'],
  { encoding: 'utf8' },
);

if (ps.error || (ps.status ?? 1) !== 0) {
  console.error('Failed to verify the Intelligence stack state after docker compose up.');
  process.exit(1);
}

/**
 * Parses `docker compose ps --format json` output, which is a JSON array on
 * older compose versions and JSON-lines on newer ones.
 *
 * @param {string} raw - Raw stdout from the ps invocation.
 * @returns {Array<{Name?: string, State?: string, Health?: string, ExitCode?: number}>}
 */
function parsePsOutput(raw) {
  const trimmed = raw.trim();
  if (trimmed === '') {
    return [];
  }
  if (trimmed.startsWith('[')) {
    return JSON.parse(trimmed);
  }
  return trimmed.split('\n').map((line) => JSON.parse(line));
}

const containers = parsePsOutput(ps.stdout);

const failed = containers.filter((container) => {
  const state = (container.State ?? '').toLowerCase();
  if (state === 'exited') {
    return (container.ExitCode ?? 1) !== 0;
  }
  if (state === 'running') {
    const health = (container.Health ?? '').toLowerCase();
    return health !== '' && health !== 'healthy';
  }
  // created / restarting / paused / dead — anything else is not a good sign.
  return true;
});

if (containers.length === 0 || failed.length > 0) {
  for (const container of failed) {
    console.error(
      `Intelligence stack container ${container.Name ?? '<unknown>'} is ${container.State ?? 'in an unknown state'} (health: ${container.Health || 'n/a'}, exit code: ${container.ExitCode ?? 'n/a'}).`,
    );
  }
  console.error('The Intelligence stack did not come up cleanly; see docker compose logs.');
  process.exit(1);
}

console.log('Intelligence stack is up (completed one-shot services ignored).');
process.exit(0);
