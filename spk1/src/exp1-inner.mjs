// Runs inside the container as gs-host: one session whose agent runs as gs-agent-1.
import { writeFileSync, readFileSync, statSync, readdirSync, existsSync } from 'node:fs';
import { randomBytes, randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { startHost, SESSIONS, sessionId } from './host.mjs';
import { connectAhp, watch, openSession, startTurn, turnEnded, answerConfirmations } from './control.mjs';

const capsOf = (pid) => Object.fromEntries(readFileSync(`/proc/${pid}/status`, 'utf8').split('\n')
  .filter((l) => /^(Uid|Gid|CapEff|CapPrm|CapAmb|NoNewPrivs):/.test(l)).map((l) => l.split(/:\s+/)));
const out = { host: capsOf('self') };
const hostToken = randomBytes(24).toString('hex');
writeFileSync('/run/gs-host/token', hostToken, { mode: 0o600 });
const ws = '/var/lib/grindstone/workspaces/run-1';
const { listener } = await startHost({ root: '/var/lib/grindstone/workspaces', port: 8720, controlToken: hostToken });
const { client } = await connectAhp(`ws://127.0.0.1:8720/?tkn=${hostToken}`, 'grindstone-control');
const uri = `ahp-session:/${randomUUID()}`;
const lines = [];
SESSIONS.set(sessionId(uri), { unixUser: 'gs-agent-1', home: '/var/lib/grindstone/agents/gs-agent-1/home',
  configDir: '/var/lib/grindstone/agents/gs-agent-1/config', claudeToken: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN, log: (l) => lines.push(l) });
const t0 = Date.now();
const s = await openSession(client, uri, ws);
const w = watch(client, s.chat);
answerConfirmations(client, s.chat, w, () => ({ approve: true }));
const turnId = startTurn(client, s.chat, 'Run the shell command `id` and show me its output. Then create a file proof.txt containing: written by the agent.');
// While the turn runs, find the agent processes and read their credentials.
const agents = new Map();
const scan = setInterval(() => {
  for (const pid of readdirSync('/proc').filter((d) => /^\d+$/.test(d))) {
    try { const c = capsOf(pid); if (c.Uid?.startsWith('10201')) agents.set(pid, { cmd: readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').slice(0, 2).join(' ').slice(0, 80), ...c }); } catch {}
  }
}, 200);
const end = await w.until(turnEnded(turnId), 150000);
clearInterval(scan);
out.turn = { end: end.type, ms: Date.now() - t0, ...(end.type === 'chat/error' ? { error: JSON.stringify(end).slice(0, 800) } : {}) };
out.agentProcesses = [...agents.values()].slice(0, 4);
out.toolCalls = w.actions.filter((x) => x.action.type === 'chat/toolCallStart').map((x) => x.action.toolName);
out.toolContent = w.actions.filter((x) => x.action.type === 'chat/toolCallContentChanged' || x.action.type === 'chat/toolCallComplete').map((x) => JSON.stringify(x.action.content ?? x.action.result).slice(0, 700));
out.deltaKeys = Object.keys(w.actions.find((x) => x.action.type === 'chat/delta')?.action ?? {});
out.reply = w.actions.filter((x) => x.action.type === 'chat/delta').map((x) => x.action.content ?? x.action.delta ?? x.action.text ?? '').join('').slice(0, 800);
// The OS boundary, tested without the model: the same switch the wrapper uses, then a read of the host's token file.
const asAgent = (cmd) => { try { return { ok: true, out: execFileSync('setpriv', ['--reuid=gs-agent-1', '--regid=gs-agent-1', '--init-groups', '--inh-caps=-all', '--ambient-caps=-all', 'sh', '-c', cmd], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim() }; } catch (e) { return { ok: false, out: `${e.stdout ?? ''}${e.stderr ?? ''}`.trim() }; } };
out.osBoundary = { id: asAgent('id'), readHostToken: asAgent('cat /run/gs-host/token'), listHostDir: asAgent('ls /run/gs-host'),
  otherRunDir: asAgent('ls -ld /var/lib/grindstone/workspaces; ls /var/lib/grindstone/workspaces') };
out.toolOutput = w.actions.filter((x) => x.action.type === 'chat/toolCallComplete').map((x) => JSON.stringify(x.action).slice(0, 600));
const proof = `${ws}/proof.txt`;
out.proof = existsSync(proof) ? { content: readFileSync(proof, 'utf8').trim(), uid: statSync(proof).uid, gid: statSync(proof).gid } : null;
out.tokenFile = { mode: (statSync('/run/gs-host/token').mode & 0o777).toString(8), uid: statSync('/run/gs-host/token').uid };
out.acpLog = lines.slice(-5);
console.log(JSON.stringify(out, null, 1));
process.exit(0);
