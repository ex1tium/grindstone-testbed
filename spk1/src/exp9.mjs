// Experiment 9: signed events. Every action the host sends the control side carries an event ID, a turn number and
// an HMAC; the control side verifies each, refuses a replay and a forgery; latency from the agent's tool-call
// update to the verified event at the control side.
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { startHost, SESSIONS, workspaceFor, sessionId, macOf } from './host.mjs';
import { connectAhp, openSession, startTurn, turnEnded, answerConfirmations, watch } from './control.mjs';

const key = randomBytes(32);
const root = mkdtempSync(join(tmpdir(), 'spk1-workspaces-'));
const controlToken = randomBytes(24).toString('hex');
await startHost({ root, port: 8722, controlToken, eventKey: key });
const { client } = await connectAhp(`ws://127.0.0.1:8722/?tkn=${controlToken}`, 'grindstone-control');

// The control side's verifier.
const seen = new Set();
const lastSeq = new Map();
function verify(params) {
  const g = params._grindstone;
  if (!g) return 'unsigned';
  const { _grindstone, ...rest } = params;
  const want = Buffer.from(macOf(key, rest, g));
  const got = Buffer.from(g.mac ?? '');
  if (want.length !== got.length || !timingSafeEqual(want, got)) return 'forged';
  if (seen.has(g.eventId)) return 'replayed';
  if ((lastSeq.get(params.channel) ?? -1) >= params.serverSeq) return 'out of order';
  seen.add(g.eventId); lastSeq.set(params.channel, params.serverSeq);
  return 'ok';
}
const received = [];
(async () => { for await (const e of client.events()) if (e.type === 'action' || e.event?.type === 'action') {
  const params = e.params ?? e.event.params; received.push({ at: Date.now(), params, verdict: verify(params) });
} })().catch(() => {});

const timingLog = join(mkdtempSync(join(tmpdir(), 'spk1-timing-')), 'agent.jsonl');
const uri = `ahp-session:/${randomUUID()}`;
SESSIONS.set(sessionId(uri), { home: mkdtempSync(join(tmpdir(), 'spk1-home-')), configDir: mkdtempSync(join(tmpdir(), 'spk1-cfg-')), claudeToken: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN, timingLog });
const s = await openSession(client, uri, workspaceFor(root, 'run-1'));
const w = watch(client, s.chat);
answerConfirmations(client, s.chat, w, () => ({ approve: true }));
const t = startTurn(client, s.chat, 'Run `ls`, then `pwd`, then `date` as three separate shell commands, then create notes.txt containing: three. Reply DONE.');
await w.until(turnEnded(t), 150000);
await new Promise((r) => setTimeout(r, 500));

const verdicts = received.reduce((m, r) => ({ ...m, [r.verdict]: (m[r.verdict] ?? 0) + 1 }), {});
// Replay and forgery against the live verifier, using real signed events.
const sample = received.find((r) => r.params.action?.type === 'chat/toolCallStart').params;
const replay = verify(sample);
const tampered = { ...sample, action: { ...sample.action, toolName: 'Bash', toolInput: 'rm -rf /' } };
const forged = verify({ ...tampered, _grindstone: { ...sample._grindstone, eventId: randomUUID() } });
const wrongKey = verify({ ...sample, serverSeq: sample.serverSeq + 10000, _grindstone: { ...sample._grindstone, eventId: randomUUID(), mac: macOf(randomBytes(32), { ...sample, serverSeq: sample.serverSeq + 10000 }, sample._grindstone) } });
const reorder = (() => { const { _grindstone, ...rest } = sample; const sig = { ...sample._grindstone, eventId: randomUUID() }; return verify({ ...rest, _grindstone: { ...sig, mac: macOf(key, rest, sig) } }); })();
// Latency: agent emitted the tool call (launcher timestamp) -> verified event at the control side.
const agentAt = existsSync(timingLog) ? readFileSync(timingLog, 'utf8').trim().split('\n').map((l) => JSON.parse(l)) : [];
const lat = agentAt.map(({ t: ta, toolCallId }) => {
  const r = received.find((x) => x.params.action?.type === 'chat/toolCallStart' && x.params.action.toolCallId === toolCallId);
  return r ? r.at - ta : null;
}).filter((x) => x !== null);
const turns = [...new Set(received.filter((r) => r.params.channel === s.chat).map((r) => r.params._grindstone?.turn))];
const out = { events: received.length, verdicts, turnNumbersOnChat: turns, attacks: { replay, forged, wrongKey, sameSeqNewId: reorder },
  latencyMs: { toolCalls: lat.length, min: Math.min(...lat), max: Math.max(...lat), mean: +(lat.reduce((a, b) => a + b, 0) / lat.length).toFixed(1) } };
out.pass = verdicts.ok === received.length && replay === 'replayed' && forged === 'forged' && wrongKey === 'forged' && reorder === 'out of order';
console.log(JSON.stringify(out, null, 1));
process.exit(0);
