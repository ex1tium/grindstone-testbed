// Experiment 4: the self-test fails with each source set in turn and passes with none; and two of them, set for
// real, change what Claude Code does (an API key outranks the subscription token; a base URL reroutes it).
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { selfTest, OUTRANKING_ENV, FOREIGN_ENV } from './selftest.mjs';
import { oneTurn, ADAPTER } from './acp-client.mjs';

const cfg = mkdtempSync(join(tmpdir(), 'spk1-cfg-'));
const managed = mkdtempSync(join(tmpdir(), 'spk1-managed-'));
const base = { CLAUDE_CODE_OAUTH_TOKEN: 'x', CLAUDE_CONFIG_DIR: cfg, GRINDSTONE_MANAGED_DIR: managed };
const rows = [];
const check = (label, env) => rows.push({ label, problems: selfTest(env) });
check('none', base);
check('no token', { ...base, CLAUDE_CODE_OAUTH_TOKEN: '' });
check('no CLAUDE_CONFIG_DIR', { ...base, CLAUDE_CONFIG_DIR: '' });
for (const name of [...OUTRANKING_ENV, ...FOREIGN_ENV]) check(name, { ...base, [name]: '1' });
for (const f of ['managed-settings.json', 'managed-mcp.json']) {
  writeFileSync(join(managed, f), '{}'); check(`/etc/claude-code/${f}`, base); rmSync(join(managed, f));
}
mkdirSync(join(managed, 'managed-settings.d')); check('/etc/claude-code/managed-settings.d', base); rmSync(join(managed, 'managed-settings.d'), { recursive: true });
for (const key of ['apiKeyHelper', 'env', 'forceLoginMethod']) {
  writeFileSync(join(cfg, 'settings.json'), JSON.stringify({ [key]: 'x' })); check(`settings.json ${key}`, base);
}
rmSync(join(cfg, 'settings.json'));
writeFileSync(join(cfg, '.credentials.json'), '{}'); check('stored login', base); rmSync(join(cfg, '.credentials.json'));
const passesClean = rows[0].problems.length === 0;
const failsEach = rows.slice(1).every((r) => r.problems.length > 0);

// Behaviour, through the adapter directly (no launcher, so no self-test in the way).
const live = async (extra) => {
  const ws = mkdtempSync(join(tmpdir(), 'spk1-ws-'));
  const env = { PATH: process.env.PATH, HOME: mkdtempSync(join(tmpdir(), 'spk1-home-')), CLAUDE_CONFIG_DIR: mkdtempSync(join(tmpdir(), 'spk1-cfg-')),
    CLAUDE_CODE_OAUTH_TOKEN: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN, ...extra };
  try {
    const r = await Promise.race([oneTurn({ cwd: ws, env, args: [ADAPTER], prompt: 'Reply with the single word OK.' }),
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout 90 s')), 90000))]);
    return `${r.stopReason}: ${r.text.trim().slice(0, 160)}`;
  } catch (e) { return `error: ${e.message.slice(0, 200)}`; }
};
const behaviour = {
  tokenOnly: await live({}),
  withBogusApiKey: await live({ ANTHROPIC_API_KEY: 'sk-ant-api03-bogus' }),
  withUnroutableBaseUrl: await live({ ANTHROPIC_BASE_URL: 'http://127.0.0.1:9' }),
};
console.log(JSON.stringify({ passesClean, failsEach, rows: rows.map((r) => [r.label, r.problems.length]), behaviour }, null, 1));
process.exit(0);
