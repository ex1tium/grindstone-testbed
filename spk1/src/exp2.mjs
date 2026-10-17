// Experiment 2: per-session MCP server and token through the wrapper backend (Q-1, ADR-16).
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { startHost, SESSIONS, workspaceFor, sessionId } from './host.mjs';
import { connectAhp, watch, openSession, startTurn, turnEnded, answerConfirmations } from './control.mjs';

const MCP = 'http://127.0.0.1:8711';
const mcpDir = new URL('../mcp/', import.meta.url).pathname;
const stub = spawn('uv', ['run', 'uvicorn', 'server:app', '--port', '8711', '--log-level', 'warning'], { cwd: mcpDir, stdio: 'ignore', env: { ...process.env, NO_PROXY: '127.0.0.1' } });
const post = async (path, body = {}) => (await fetch(MCP + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })).json();
for (let i = 0; i < 100; i++) { try { await fetch(MCP + '/admin/calls'); break; } catch { await new Promise((r) => setTimeout(r, 200)); } }

const root = mkdtempSync(join(tmpdir(), 'spk1-workspaces-'));
const controlToken = randomBytes(24).toString('hex');
const { listener } = await startHost({ root, port: 8720, controlToken });
const { client } = await connectAhp(`ws://127.0.0.1:8720/?tkn=${controlToken}`, 'grindstone-control');

const ASK = 'Call the whoami tool of the grindstone MCP server, then reply with only the value of its "session" field, or with FAILED and the error if the call fails.';
const sessions = [];
for (const name of ['A', 'B']) {
  const uri = `ahp-session:/${randomUUID()}`;
  const { token } = await post('/admin/tokens', { session: name, task: `T-${name}`, run: `R-${name}`, capability: 'implement' });
  const lines = [];
  SESSIONS.set(sessionId(uri), {
    home: mkdtempSync(join(tmpdir(), 'spk1-home-')), configDir: mkdtempSync(join(tmpdir(), 'spk1-cfg-')),
    claudeToken: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN, mcp: { url: `${MCP}/kernel/mcp`, token }, log: (l) => lines.push(l),
  });
  const s = await openSession(client, uri, workspaceFor(root, `run-${name}`));
  const w = watch(client, s.chat);
  answerConfirmations(client, s.chat, w, () => ({ approve: true }));
  sessions.push({ name, token, s, w, lines });
}
const turn = async (x) => {
  const id = startTurn(client, x.s.chat, ASK);
  await x.w.until(turnEnded(id), 120000);
  return x.w.actions.filter((a) => a.action.turnId === id && a.action.type === 'chat/responsePart').map((a) => JSON.stringify(a.action.part ?? a.action).slice(0, 200)).join(' ')
    || x.w.actions.filter((a) => a.action.turnId === id && a.action.type === 'chat/delta').map((a) => a.action.content).join('');
};
const out = { phase1: {}, phase2: {} };
const r1 = await Promise.all(sessions.map(turn));            // concurrently
sessions.forEach((x, i) => { out.phase1[x.name] = r1[i]; });
await post(`/admin/revoke/${sessions[0].token}`);             // revoke A's token mid-session
const r2 = await Promise.all(sessions.map(turn));
sessions.forEach((x, i) => { out.phase2[x.name] = r2[i]; });
out.server = await (await fetch(MCP + '/admin/calls')).json();
out.toolCalls = Object.fromEntries(sessions.map((x) => [x.name, x.w.actions.filter((a) => a.action.type === 'chat/toolCallStart').map((a) => a.action.toolName)]));
out.acpLog = Object.fromEntries(sessions.map((x) => [x.name, x.lines.filter((l) => /mcp|grindstone|left out/i.test(l)).slice(0, 5)]));
const calls = out.server.calls;
out.pass = calls.filter((c) => c.session === 'A').length === 1 && calls.filter((c) => c.session === 'B').length === 2
  && out.server.refused.some((r) => r.known);
console.log(JSON.stringify(out, null, 1));
stub.kill(); await client.shutdown(); process.exit(0);
