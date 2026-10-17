// SPK-5 harness: every experiment at once on the SPK-1 host and the SPK-5 stub kernel.
// SPK5_MINUTE (seconds, default 60) scales every duration; SPK5_PROGRESS (seconds, default 30) is the keepalive.
import { mkdtempSync, writeFileSync, readFileSync, existsSync, readdirSync, readlinkSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn, execSync } from 'node:child_process';
import { createServer } from 'node:http';
import { randomBytes, randomUUID } from 'node:crypto';
import { startHost, SESSIONS, sessionId } from '../spk1/src/host.mjs';
import { connectAhp, watch, openSession, startTurn, turnEnded, answerConfirmations } from '../spk1/src/control.mjs';
import { oneTurn } from '../spk1/src/acp-client.mjs';

const M = Number(process.env.SPK5_MINUTE ?? 60);
const P = Number(process.env.SPK5_PROGRESS ?? 30);
const LIVENESS = 2 * M;
const OUT = join(new URL('.', import.meta.url).pathname, 'results', process.env.SPK5_RUN ?? new Date().toISOString().replace(/[:.]/g, '-'));
mkdirSync(OUT, { recursive: true });
const t0 = Date.now();
const now = () => +((Date.now() - t0) / 1000).toFixed(1);
const record = {};
const note = (k, v) => { record[k] = v; writeFileSync(join(OUT, 'record.json'), JSON.stringify({ M, P, record }, null, 1)); console.log(`[${now()}s] ${k}: ${JSON.stringify(v).slice(0, 300)}`); };
const ONLY = process.env.SPK5_ONLY?.split(',');
const on = (name) => !ONLY || ONLY.includes(name);
const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000));

// --- the stub kernel and the consult agent endpoint ---------------------------------------------------------
const KERNEL = 'http://127.0.0.1:8740';
const kernel = spawn('uv', ['run', '--project', '../spk1/mcp', 'uvicorn', 'kernel:app', '--port', '8740', '--log-level', 'warning'],
  { cwd: new URL('.', import.meta.url).pathname, stdio: ['ignore', 'ignore', 'inherit'], env: { ...process.env, NO_PROXY: '127.0.0.1' } });
const post = async (path, body = {}) => (await fetch(KERNEL + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })).json();
const kstate = async () => (await fetch(KERNEL + '/admin/state')).json();
for (let i = 0; i < 150; i++) { try { await kstate(); break; } catch { await sleep(0.2); } }

const fixture = mkdtempSync(join(tmpdir(), 'spk5-fixture-'));
execSync(`git -C ${new URL('..', import.meta.url).pathname} archive main src tests intent pyproject.toml README.md | tar -x -C ${fixture}`);
const consultServer = createServer(async (req, res) => {
  let body = ''; for await (const c of req) body += c;
  const { question } = JSON.parse(body);
  const ts = Date.now();
  const r = await oneTurn({ cwd: fixture, prompt: `${question}\nAnswer in at most three sentences.`,
    env: { PATH: process.env.PATH, HOME: mkdtempSync(join(tmpdir(), 'spk5-home-')), CLAUDE_CONFIG_DIR: mkdtempSync(join(tmpdir(), 'spk5-cfg-')),
      CLAUDE_CODE_OAUTH_TOKEN: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN } });
  const usage = r.updates.filter((u) => u.sessionUpdate === 'usage_update').at(-1);
  const cost = { wallS: (Date.now() - ts) / 1000, toolCalls: r.updates.filter((u) => u.sessionUpdate === 'tool_call').length, usage: usage ?? null };
  note('consultAgent', cost);
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({ answer: r.text.trim(), cost }));
});
await new Promise((r) => consultServer.listen(8742, '127.0.0.1', r));

// --- the host ------------------------------------------------------------------------------------------------
const root = mkdtempSync(join(tmpdir(), 'spk5-workspaces-'));
const controlToken = randomBytes(24).toString('hex');
await startHost({ root, port: 8741, controlToken });
const { client } = await connectAhp(`ws://127.0.0.1:8741/?tkn=${controlToken}`, 'grindstone-control');
const CONFIGURED = { MCP_TOOL_TIMEOUT: String(3 * 3600 * 1000) };  // above the 2-hour ceiling; idle window left at its default

const sessions = {};
async function session(name, { delay, progress = P, ceiling = 60 * M, env = CONFIGURED, consultUrl, workspace } = {}) {
  const uri = `ahp-session:/${randomUUID()}`;
  const { token } = await post('/admin/tokens', { session: name, delay_s: delay ?? 0, progress_s: progress, ceiling_s: ceiling, ...(consultUrl ? { consult_url: consultUrl } : {}) });
  const ws = workspace ?? join(root, name);
  mkdirSync(ws, { recursive: true });
  SESSIONS.set(sessionId(uri), { home: mkdtempSync(join(tmpdir(), 'spk5-home-')), configDir: mkdtempSync(join(tmpdir(), 'spk5-cfg-')),
    claudeToken: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN, mcp: { url: `${KERNEL}/kernel/mcp`, token }, env });
  const s = await openSession(client, uri, ws);
  const w = watch(client, s.chat);
  answerConfirmations(client, s.chat, w, () => ({ approve: true }));
  return (sessions[name] = { name, uri, ws, s, w });
}
const ASK = (q) => `Call the ask tool of the grindstone MCP server with the question "${q}". It may take a very long time to answer; wait for it. `
  + 'Then reply with exactly one line: ANSWER: <its answer>, or WAITING if its status is consult_ceiling, or FAILED: <the error> if the call fails.';
async function turn(x, prompt, ms = (60 * M + 600) * 1000) {
  const id = startTurn(client, x.s.chat, prompt);
  const startedAt = now();
  const end = await x.w.until(turnEnded(id), ms).catch((e) => ({ type: `harness timeout: ${e.message}` }));
  const reply = x.w.actions.filter((a) => a.action.type === 'chat/delta' && a.action.turnId === id).map((a) => a.action.content).join('').trim();
  const tool = x.w.actions.filter((a) => a.action.type === 'chat/toolCallComplete' && a.action.turnId === id).map((a) => JSON.stringify(a.action.result).slice(0, 300));
  return { startedAt, endedAt: now(), end: end.type, error: end.part?.error?.message?.slice(0, 300), reply: reply.slice(-400), tool };
}

// --- liveness (experiment 5) -----------------------------------------------------------------------------------
const flagged = {};
const liveness = setInterval(async () => {
  const st = await kstate().catch(() => null);
  if (!st) return;
  for (const x of Object.values(sessions)) {
    if (x.done) continue;
    if (!x.armed && x.name !== 'S1' && x.w.actions.some((a) => a.action.type === 'chat/toolCallStart')) x.armed = true;
    if (!x.armed) continue;
    const last = x.w.actions.at(-1)?.at ?? t0;
    if ((Date.now() - last) / 1000 > LIVENESS && !(st.open[x.name] > 0) && !flagged[x.name]) {
      flagged[x.name] = now();
      note(`stalled:${x.name}`, { at: flagged[x.name], silentFor: (Date.now() - last) / 1000 });
    }
  }
}, 5000);

// --- the experiments -------------------------------------------------------------------------------------------
const exp1 = (on('exp1') ? [2, 10, 30, 45] : []).map(async (mins) => {
  const x = await session(`L${mins}`, { delay: mins * M });
  note(`exp1:L${mins}`, await turn(x, ASK(`long question ${mins}`)));
  x.done = true;
});
const exp2 = (async () => {
  if (!on('exp2')) return;
  const x = await session('N10', { delay: 10 * M, progress: 0 });
  note('exp2:N10-no-keepalive', await turn(x, ASK('quiet question')));
  x.done = true;
})();
const exp1Default = (async () => {
  if (!on('exp1')) return;
  const x = await session('D10', { delay: 10 * M, env: {} });
  note('exp1:D10-default-config', await turn(x, ASK('default question')));
  x.done = true;
})();
const exp3 = (async () => {
  if (!on('exp3')) return;
  const q = 'joined question';
  const a = await session('J1', { delay: 30 * M });
  const first = turn(a, ASK(q));
  await sleep(5 * M);
  await client.request('disposeSession', { channel: a.uri }).catch((e) => note('exp3:dispose-error', e.message));
  a.done = true;
  note('exp3:J1-disposed', { at: now(), turn: await Promise.race([first, sleep(20).then(() => 'still open after dispose')]) });
  const b = await session('J2', { delay: 30 * M, workspace: a.ws });
  note('exp3:J2', await turn(b, ASK(q)));
  b.done = true;
})();
const exp4 = (async () => {
  if (!on('exp4')) return;
  const q = 'ceiling question';
  const a = await session('C1', { delay: 10 * M, ceiling: 5 * M });
  note('exp4:C1', await turn(a, ASK(q)));
  a.done = true;
  // The run waits; the answer resumes it in a new session with the answer in context.
  const cid = (await kstate()).log.find((l) => l.kind === 'ceiling' && l.session === 'C1')?.consult;
  let consult;
  for (let i = 0; cid && i < (20 * M) / 5; i++) {
    consult = (await kstate()).consults.find((c) => c.id === cid && c.answer);
    if (consult) break;
    await sleep(5);
  }
  if (!consult) { note('exp4:no-ceiling-or-answer', { cid }); a.done = true; return; }
  note('exp4:answer-arrived', { at: now(), consult: consult.id });
  const b = await session('C2', { workspace: a.ws });
  note('exp4:C2-resumed', await turn(b, `You are resuming a run that waited on a consult. The consult answered: "${consult.answer}". Write that answer, exactly, into answer.txt, then reply DONE.`));
  note('exp4:answer.txt', existsSync(join(a.ws, 'answer.txt')) ? readFileSync(join(a.ws, 'answer.txt'), 'utf8').trim() : null);
  b.done = true;
})();
const exp5 = (async () => {
  if (!on('exp5')) return;
  // A session that goes silent with no open tool call: its agent processes are stopped mid-turn.
  const x = await session('S1');
  const id = startTurn(client, x.s.chat, 'Run the shell command `sleep 3` four times, one call at a time, then reply DONE.');
  await x.w.until((a) => a.type === 'chat/toolCallStart', 120000);
  const pids = readdirSync('/proc').filter((p) => /^\d+$/.test(p)).filter((p) => { try { return readlinkSync(`/proc/${p}/cwd`) === x.ws; } catch { return false; } });
  for (const p of pids) try { process.kill(Number(p), 'SIGSTOP'); } catch {}
  x.armed = true;
  note('exp5:S1-stopped', { at: now(), pids: pids.length });
  await sleep(LIVENESS + 30);
  note('exp5:S1-flagged', flagged.S1 ?? null);
  for (const p of pids) try { process.kill(Number(p), 'SIGKILL'); } catch {}
  x.done = true;
})();
const exp6 = (async () => {
  if (!on('exp6')) return;
  const x = await session('K1');
  note('exp6:K1', await turn(x, 'Call the load_skill tool of the grindstone MCP server with the name "review", then follow the instructions it returns.', 300000));
  note('exp6:K1-next-turn', await turn(x, 'Call the review_codeword tool of the grindstone MCP server and reply with exactly the codeword it returns, prefixed with CODEWORD: .', 300000));
  note('exp6:tools', x.w.actions.filter((a) => a.action.type === 'chat/toolCallStart').map((a) => a.action.toolName));
  note('exp6:tools_list', (await kstate()).log.filter((l) => l.session === 'K1'));
  x.done = true;
})();
const exp7 = (async () => {
  if (!on('exp7')) return;
  const q = 'In the fixture project, which functions does src/testbed/text.py define, and what does each do?';
  const a = await session('X1', { consultUrl: 'http://127.0.0.1:8742/consult' });
  note('exp7:X1-uncached', await turn(a, ASK(q), 900000));
  a.done = true;
  const b = await session('X2', { consultUrl: 'http://127.0.0.1:8742/consult' });
  note('exp7:X2-cached', await turn(b, ASK(q), 900000));
  b.done = true;
})();

await Promise.allSettled([...exp1, exp2, exp1Default, exp3, exp4, exp5, exp6, exp7]);
clearInterval(liveness);
const st = await kstate();
note('kernel', { consults: st.consults.map(({ id, delivered, runs, cost }) => ({ id, delivered, runs, cost })), log: st.log });
note('flagged', flagged);
writeFileSync(join(OUT, 'DONE'), String(now()));
kernel.kill(); consultServer.close();
process.exit(0);
