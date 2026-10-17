#!/usr/bin/env node
// Grindstone's vendored ACP agent plugin, as a launcher: claude-agent-acp behind a stdio proxy that
// merges Grindstone's per-session Claude Code options into every session/new, load and resume.
// GRINDSTONE_ACP_OPTIONS (JSON) holds them; the default keeps every settings source out (ADR-18, ADR-39).
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { selfTest } from './selftest.mjs';

const problems = selfTest(process.env);
if (problems.length > 0) {
  process.stderr.write(`grindstone launcher: refusing to start: ${problems.join('; ')}\n`);
  process.exit(78);
}
const adapter = new URL('../node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js', import.meta.url).pathname;
const options = { settingSources: [], ...JSON.parse(process.env.GRINDSTONE_ACP_OPTIONS ?? '{}') };
const env = { ...process.env };
delete env.GRINDSTONE_ACP_OPTIONS;
const timing = process.env.SPK1_TIMING_LOG;
delete env.SPK1_TIMING_LOG;
const child = spawn(process.execPath, [adapter], { stdio: ['pipe', timing ? 'pipe' : 'inherit', 'inherit'], env });
if (timing) {
  // Spike only: when the agent emitted each tool call, for experiment 9's latency.
  const { appendFileSync } = await import('node:fs');
  createInterface({ input: child.stdout }).on('line', (line) => {
    const t = Date.now();
    process.stdout.write(line + '\n');
    const m = /"sessionUpdate":"tool_call","toolCallId":"([^"]+)"/.exec(line) ?? (line.includes('"sessionUpdate":"tool_call"') ? /"toolCallId":"([^"]+)"/.exec(line) : null);
    if (m) appendFileSync(timing, JSON.stringify({ t, toolCallId: m[1] }) + '\n');
  });
}
const REWRITE = new Set(['session/new', 'session/load', 'session/resume', 'session/fork']);
createInterface({ input: process.stdin }).on('line', (line) => {
  try {
    const msg = JSON.parse(line);
    if (REWRITE.has(msg.method)) {
      const meta = msg.params._meta ?? {};
      // Grindstone's options win over anything a client sent for the same key.
      msg.params._meta = { ...meta, claudeCode: { ...(meta.claudeCode ?? {}), options: { ...(meta.claudeCode?.options ?? {}), ...options } } };
      line = JSON.stringify(msg);
    }
  } catch { /* not JSON: pass through */ }
  child.stdin.write(line + '\n');
});
process.stdin.on('end', () => child.stdin.end());
child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
