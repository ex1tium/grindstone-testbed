// Experiments 6 and 7: a browser-equivalent AHP client on a viewer token watches the session live and steers it;
// a permission request reaches the control side as an event, the viewer's answer is refused, the control's honoured.
import { mkdtempSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomBytes, randomUUID } from 'node:crypto';
import { startHost, SESSIONS, workspaceFor, sessionId } from './host.mjs';
import { connectAhp, watch, openSession, startTurn, turnEnded } from './control.mjs';

const asked = [];
const viewer = { id: 'operator-browser', roles: ['viewer'], can: (grant) => { asked.push(grant); return grant !== 'chat:answer'; } };
const root = mkdtempSync(join(tmpdir(), 'spk1-workspaces-'));
const controlToken = randomBytes(24).toString('hex');
const viewerToken = randomBytes(24).toString('hex');
await startHost({ root, port: 8720, controlToken, viewers: new Map([[viewerToken, viewer]]) });
const control = await connectAhp(`ws://127.0.0.1:8720/?tkn=${controlToken}`, 'grindstone-control');
const ws = workspaceFor(root, 'run-1');
const uri = `ahp-session:/${randomUUID()}`;
SESSIONS.set(sessionId(uri), { home: mkdtempSync(join(tmpdir(), 'spk1-home-')), configDir: mkdtempSync(join(tmpdir(), 'spk1-cfg-')), claudeToken: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN });
const s = await openSession(control.client, uri, ws);
const cw = watch(control.client, s.chat);

// The viewer attaches to the same live session.
const v = await connectAhp(`ws://127.0.0.1:8720/?tkn=${viewerToken}`, 'browser-1');
const vEvents = [];
(async () => { for await (const e of v.client.events()) if (e.type !== 'action') vEvents.push(JSON.stringify(e).slice(0, 300)); })().catch(() => {});
await v.client.subscribe(uri);
await v.client.subscribe(s.chat);
const vw = watch(v.client, s.chat);
const out = { protocol: { control: control.init.protocolVersion, viewer: v.init.protocolVersion } };

const pendingTool = async (w, turnId) => (await w.until((a) => a.type === 'chat/toolCallReady' && !a.confirmed && (!turnId || a.turnId === turnId), 120000));
const t1 = startTurn(control.client, s.chat, 'Create first.txt containing exactly: one. Reply DONE when finished.');
const ready = await pendingTool(cw);
await vw.until((a) => a.type === 'chat/toolCallReady' && a.toolCallId === ready.toolCallId, 10000);
out.permissionEvent = { seenByControl: true, tool: ready.confirmationTitle, options: ready.options.map((o) => o.id) };
// The viewer answers first.
let viewerDispatchError = null;
try { v.client.dispatch(s.chat, { type: 'chat/toolCallConfirmed', turnId: ready.turnId, toolCallId: ready.toolCallId, approved: true, confirmed: 'user-action', selectedOptionId: 'allow-once' }); }
catch (e) { viewerDispatchError = e.message; }
await new Promise((r) => setTimeout(r, 3000));
const confirmedAfterViewer = cw.actions.some((x) => x.action.type === 'chat/toolCallConfirmed' && x.action.toolCallId === ready.toolCallId);
out.viewerAnswer = { dispatchError: viewerDispatchError, confirmedWithin3s: confirmedAfterViewer, fileExists: existsSync(join(ws, 'first.txt')), viewerNotices: vEvents.filter((e) => /reject|refus|may not/i.test(e)).slice(-2) };
// The control side answers.
control.client.dispatch(s.chat, { type: 'chat/toolCallConfirmed', turnId: ready.turnId, toolCallId: ready.toolCallId, approved: true, confirmed: 'user-action', selectedOptionId: 'allow-once' });
const e1 = await cw.until(turnEnded(t1), 120000);
const confirmedBy = cw.actions.find((x) => x.action.type === 'chat/toolCallConfirmed' && x.action.toolCallId === ready.toolCallId);
out.controlAnswer = { turnEnd: e1.type, file: existsSync(join(ws, 'first.txt')) ? readFileSync(join(ws, 'first.txt'), 'utf8').trim() : null };

// Steering from the viewer: a follow-up message, answered by the control side.
const t2 = startTurn(v.client, s.chat, 'Now also create second.txt containing exactly: steered. Reply DONE.');
const ready2 = await pendingTool(cw, t2).catch(() => null);
if (ready2) control.client.dispatch(s.chat, { type: 'chat/toolCallConfirmed', turnId: ready2.turnId, toolCallId: ready2.toolCallId, approved: true, confirmed: 'user-action', selectedOptionId: 'allow-once' });
const e2 = await cw.until(turnEnded(t2), 120000).catch((e) => ({ type: e.message }));
const startedSeenByControl = cw.actions.find((x) => x.action.type === 'chat/turnStarted' && x.action.turnId === t2);
out.steering = { turnStartedSeenByControl: !!startedSeenByControl, turnEnd: e2.type, file: existsSync(join(ws, 'second.txt')) ? readFileSync(join(ws, 'second.txt'), 'utf8').trim() : null };

await new Promise((r) => setTimeout(r, 1000));  // let the last actions reach the viewer
// Live transcript: the viewer saw the same actions as the control side, and how far behind.
const key = (a) => `${a.type}|${a.turnId ?? ''}|${a.toolCallId ?? ''}|${a.partId ?? ''}|${a.content ?? ''}`;
const cAt = new Map(cw.actions.map((x) => [key(x.action), x.at]));
const lags = vw.actions.map((x) => x.at - (cAt.get(key(x.action)) ?? x.at));
out.transcript = { controlActions: cw.actions.length, viewerActions: vw.actions.length, viewerMissing: cw.actions.filter((x) => !vw.actions.some((y) => key(y.action) === key(x.action))).map((x) => x.action.type).slice(0, 10), maxLagMs: Math.max(...lags) };
out.grantsAskedOfViewer = [...new Set(asked)];
out.usersDirectory = !process.env.SPK1_NO_USERS;
out.pass = !out.viewerAnswer.confirmedWithin3s && out.controlAnswer.file === 'one' && out.steering.file === 'steered' && out.transcript.viewerMissing.length === 0;
console.log(JSON.stringify(out, null, 1));
process.exit(0);
