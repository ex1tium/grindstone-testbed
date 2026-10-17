// Experiment 8: kill the host process during a turn; what the control side sees after restart; a new session
// on the same workspace continues; what is lost, and whether the agent outlives its host.
import { mkdtempSync, writeFileSync, existsSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn, execSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { sessionId } from './host.mjs';
import { connectAhp, watch, openSession, startTurn, turnEnded, answerConfirmations } from './control.mjs';

const root = mkdtempSync(join(tmpdir(), 'spk1-workspaces-'));
const ws = join(root, 'run-1');
const records = join(mkdtempSync(join(tmpdir(), 'spk1-rec-')), 'records.json');
const controlToken = randomBytes(24).toString('hex');
const recs = {};
const startHostProc = () => new Promise((resolve) => {
  const p = spawn(process.execPath, [new URL('./host-proc.mjs', import.meta.url).pathname], { stdio: ['ignore', 'pipe', 'inherit'],
    env: { PATH: process.env.PATH, GRINDSTONE_CLAUDE_OAUTH_TOKEN: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN, SPK1_RECORDS: records, SPK1_ROOT: root, SPK1_PORT: '8721', SPK1_CONTROL_TOKEN: controlToken } });
  p.stdout.on('data', (d) => { if (String(d).includes('ready')) resolve(p); });
});
const newSession = (name) => {
  const uri = `ahp-session:/${randomUUID()}`;
  recs[sessionId(uri)] = { home: mkdtempSync(join(tmpdir(), 'spk1-home-')), configDir: mkdtempSync(join(tmpdir(), 'spk1-cfg-')) };
  writeFileSync(records, JSON.stringify(recs));
  return uri;
};
const agentProcs = () => execSync('ps -eo pid,ppid,args', { encoding: 'utf8' }).split('\n').filter((l) => /claude-agent-acp|launcher\.mjs|claude-agent-sdk/.test(l) && !/ps -eo/.test(l)).map((l) => l.trim().slice(0, 120));

const out = {};
let host = await startHostProc();
let c = await connectAhp(`ws://127.0.0.1:8721/?tkn=${controlToken}`, 'grindstone-control');
writeFileSync(records, '{}');
const { mkdirSync } = await import('node:fs'); mkdirSync(ws, { recursive: true });
const uri1 = newSession('one');
const s1 = await openSession(c.client, uri1, ws);
const w1 = watch(c.client, s1.chat);
answerConfirmations(c.client, s1.chat, w1, () => ({ approve: true }));
const t1 = startTurn(c.client, s1.chat, 'Run the shell command `sleep 25 && echo late > late.txt` (it takes 25 seconds), then create done.txt containing: done.');
await w1.until((a) => a.type === 'chat/toolCallStart' && a.toolName === 'Bash', 120000);
await new Promise((r) => setTimeout(r, 3000));
out.beforeKill = { agents: agentProcs().length };
host.kill('SIGKILL');
const killedAt = Date.now();
await new Promise((r) => setTimeout(r, 1500));
out.afterKill = { agentsStillRunning: agentProcs() };
// Restart the host; reconnect; what does it report?
host = await startHostProc();
c = await connectAhp(`ws://127.0.0.1:8721/?tkn=${controlToken}`, 'grindstone-control-2');
const rootSnap = c.init.snapshots?.find((x) => x.resource === 'ahp-root://' || x.uri === 'ahp-root://') ?? c.init.snapshots?.[0];
out.afterRestart = { rootKeys: Object.keys(rootSnap?.state ?? rootSnap ?? {}), sessions: JSON.stringify(rootSnap?.state?.sessions ?? rootSnap?.state?.activeSessions ?? null).slice(0, 300) };
try { const r = await c.client.request('listSessions', {}); out.afterRestart.listSessions = JSON.stringify(r).slice(0, 300); } catch (e) { out.afterRestart.listSessions = `error: ${e.message}`; }
try { await c.client.subscribe(uri1); out.afterRestart.oldSessionSubscribe = 'ok'; } catch (e) { out.afterRestart.oldSessionSubscribe = `error: ${e.message}`; }
// A new session on the same workspace continues.
const uri2 = newSession('two');
const s2 = await openSession(c.client, uri2, ws);
const w2 = watch(c.client, s2.chat);
answerConfirmations(c.client, s2.chat, w2, () => ({ approve: true }));
const t2 = startTurn(c.client, s2.chat, 'A previous session in this folder was interrupted. List the files here, then create done.txt containing: continued, unless done.txt already exists. Reply with the file list.');
const e2 = await w2.until(turnEnded(t2), 120000);
out.newSession = { end: e2.type, reply: w2.actions.filter((x) => x.action.type === 'chat/delta').map((x) => x.action.content).join('').slice(0, 300) };
// Did the orphaned agent finish its command after the host died?
await new Promise((r) => setTimeout(r, Math.max(0, 30000 - (Date.now() - killedAt))));
out.workspace = Object.fromEntries(readdirSync(ws).map((f) => [f, readFileSync(join(ws, f), 'utf8').trim()]));
out.orphansAfter30s = agentProcs();
host.kill('SIGKILL');
console.log(JSON.stringify(out, null, 1));
process.exit(0);
