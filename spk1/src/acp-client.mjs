// Minimal raw ACP client over stdio, for the spike experiments.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

export const ADAPTER = new URL('../node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js', import.meta.url).pathname;
export const LAUNCHER = new URL('./launcher.mjs', import.meta.url).pathname;

/** Start an ACP agent process; returns { call, close, updates, text(), permissions, stderr() }. */
export function connect({ command = process.execPath, args = [LAUNCHER], cwd, env, onPermission, spawnOptions = {} }) {
  const child = spawn(command, args, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'], ...spawnOptions });
  let stderr = '';
  child.stderr.on('data', (d) => { stderr += d; });
  let next = 1;
  const pending = new Map();
  const updates = [];
  const permissions = [];
  const send = (msg) => child.stdin.write(JSON.stringify(msg) + '\n');
  const exited = new Promise((resolve) => child.on('exit', (code, signal) => {
    for (const p of pending.values()) p.reject(new Error(`agent exited ${code ?? signal}: ${stderr.slice(-800)}`));
    resolve({ code, signal });
  }));
  createInterface({ input: child.stdout }).on('line', async (line) => {
    let msg;
    try { msg = JSON.parse(line); } catch { return; }
    if (msg.id !== undefined && msg.method === undefined && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id);
      msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
    } else if (msg.method === 'session/update') {
      updates.push(msg.params.update);
    } else if (msg.method === 'session/request_permission') {
      permissions.push(msg.params);
      const answer = onPermission ? await onPermission(msg.params) : msg.params.options.find((o) => o.kind === 'allow_once');
      send({ jsonrpc: '2.0', id: msg.id, result: answer ? { outcome: { outcome: 'selected', optionId: answer.optionId } } : { outcome: { outcome: 'cancelled' } } });
    } else if (msg.id !== undefined) {
      send({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'not supported by the spike client' } });
    }
  });
  const call = (method, params) => new Promise((resolve, reject) => {
    const id = next++;
    pending.set(id, { resolve, reject });
    send({ jsonrpc: '2.0', id, method, params });
  });
  const text = () => updates.filter((u) => u.sessionUpdate === 'agent_message_chunk' && u.content?.type === 'text').map((u) => u.content.text).join('');
  return { child, call, updates, permissions, text, stderr: () => stderr, exited, close: () => child.kill() };
}

/** One session, one prompt. */
export async function oneTurn({ cwd, env, prompt, meta, mcpServers = [], ...rest }) {
  const c = connect({ cwd, env, ...rest });
  try {
    await c.call('initialize', { protocolVersion: 1, clientCapabilities: {} });
    const session = await c.call('session/new', { cwd, mcpServers, ...(meta ? { _meta: meta } : {}) });
    const res = await c.call('session/prompt', { sessionId: session.sessionId, prompt: [{ type: 'text', text: prompt }] });
    return { stopReason: res.stopReason, text: c.text(), updates: c.updates, permissions: c.permissions, stderr: c.stderr() };
  } finally {
    c.close();
  }
}
