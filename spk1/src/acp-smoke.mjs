// SPK-1 smoke test: raw ACP over stdio to claude-agent-acp, one prompt, in a fresh workspace.
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';

const ws = mkdtempSync(join(tmpdir(), 'spk1-ws-'));
const home = mkdtempSync(join(tmpdir(), 'spk1-home-'));
const env = { PATH: process.env.PATH, HOME: home, CLAUDE_CODE_OAUTH_TOKEN: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN };
const bin = new URL('../node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js', import.meta.url).pathname;
const child = spawn(process.execPath, [bin], { cwd: ws, env, stdio: ['pipe', 'pipe', 'pipe'] });
let stderr = '';
child.stderr.on('data', (d) => { stderr += d; });

let next = 1;
const pending = new Map();
const updates = [];
const send = (msg) => child.stdin.write(JSON.stringify(msg) + '\n');
const call = (method, params) => new Promise((resolve, reject) => {
  const id = next++;
  pending.set(id, { resolve, reject });
  send({ jsonrpc: '2.0', id, method, params });
});
createInterface({ input: child.stdout }).on('line', (line) => {
  const msg = JSON.parse(line);
  if (msg.id !== undefined && pending.has(msg.id) && msg.method === undefined) {
    const p = pending.get(msg.id); pending.delete(msg.id);
    msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
  } else if (msg.method === 'session/update') {
    updates.push(msg.params.update.sessionUpdate);
  } else if (msg.method === 'session/request_permission') {
    const allow = msg.params.options.find((o) => o.kind === 'allow_once') ?? msg.params.options[0];
    send({ jsonrpc: '2.0', id: msg.id, result: { outcome: { outcome: 'selected', optionId: allow.optionId } } });
  } else if (msg.id !== undefined) {
    send({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'not supported' } });
  }
});

const t0 = Date.now();
try {
  const init = await call('initialize', { protocolVersion: 1, clientCapabilities: {} });
  const session = await call('session/new', { cwd: ws, mcpServers: [] });
  const t1 = Date.now();
  const res = await call('session/prompt', { sessionId: session.sessionId, prompt: [{ type: 'text', text: 'Create a file named hello.txt in the current directory containing exactly: hi from spk1. Then reply DONE.' }] });
  const file = join(ws, 'hello.txt');
  console.log(JSON.stringify({
    agent: init.agentInfo, protocolVersion: init.protocolVersion, authMethods: (init.authMethods ?? []).map((m) => m.id),
    stopReason: res.stopReason, sessionStartMs: t1 - t0, turnMs: Date.now() - t1,
    file: existsSync(file) ? readFileSync(file, 'utf8').trim() : null,
    updateKinds: [...new Set(updates)],
  }, null, 2));
} catch (e) {
  console.log('ERROR', e.message, '\nstderr tail:', stderr.slice(-1500));
} finally {
  child.kill();
}
