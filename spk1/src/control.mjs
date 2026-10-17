// The control side's AHP client (spike): create a session, run a turn, watch actions, answer confirmations.
import { AhpClient } from '@microsoft/agent-host-protocol/client';
import { WebSocketTransport } from '@microsoft/agent-host-protocol/ws';
import { SUPPORTED_PROTOCOL_VERSIONS } from '@microsoft/agent-host-protocol';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';

export async function connectAhp(url, clientId) {
  const transport = await WebSocketTransport.connect(url);
  const client = new AhpClient(transport);
  client.connect();
  const init = await client.initialize({ clientId, protocolVersions: [...SUPPORTED_PROTOCOL_VERSIONS], initialSubscriptions: ['ahp-root://'] });
  return { client, init };
}

/** Collect every action on a channel; resolve waiters on predicates. */
export function watch(client, uri) {
  const actions = [];
  const waiters = [];
  const sub = client.attachSubscription(uri);
  (async () => {
    for await (const event of sub) {
      if (event.type !== 'action') continue;
      const a = event.params.action;
      actions.push({ at: Date.now(), action: a });
      for (const w of [...waiters]) if (w.pred(a)) { waiters.splice(waiters.indexOf(w), 1); w.resolve(a); }
    }
  })().catch(() => {});
  const until = (pred, ms = 180000) => new Promise((resolve, reject) => {
    const hit = actions.find((x) => pred(x.action));
    if (hit) return resolve(hit.action);
    const w = { pred, resolve };
    waiters.push(w);
    setTimeout(() => { const i = waiters.indexOf(w); if (i >= 0) { waiters.splice(i, 1); reject(new Error(`timeout waiting on ${uri}`)); } }, ms);
  });
  return { actions, until };
}

export async function createSession(client, { provider = 'claude', workspace }) {
  const uri = `ahp-session:/${randomUUID()}`;
  return uri;
}

export async function openSession(client, uri, workspace) {
  await client.request('createSession', { channel: uri, provider: 'claude', workingDirectories: [pathToFileURL(workspace).href] });
  const { result } = await client.subscribe(uri);
  const state = result.snapshot?.state ?? result.state ?? result;
  const chat = state.defaultChat ?? state.chats?.[0]?.resource ?? state.chats?.[0]?.uri;
  await client.subscribe(chat);
  return { uri, chat, state };
}

export function startTurn(client, chat, text) {
  const turnId = randomUUID();
  client.dispatch(chat, { type: 'chat/turnStarted', turnId, startedAt: new Date().toISOString(), message: { text, origin: { kind: 'user' } } });
  return turnId;
}

export const turnEnded = (turnId) => (a) => ['chat/turnComplete', 'chat/turnCancelled', 'chat/error'].includes(a.type) && a.turnId === turnId;
export const textOf = (actions, turnId) => actions.filter((x) => x.action.type === 'chat/delta' && (!turnId || x.action.turnId === turnId)).map((x) => x.action.content ?? x.action.text ?? '').join('');

/** Answer every confirmation on this chat with decide(action) -> { approve: bool, optionKind?: 'approve'|'deny' }. */
export function answerConfirmations(client, chat, w, decide, seen = new Set()) {
  const tick = setInterval(() => {
    for (const { action: a } of w.actions) {
      if (a.type !== 'chat/toolCallReady' || a.confirmed || seen.has(a.toolCallId)) continue;
      seen.add(a.toolCallId);
      const d = decide(a);
      const option = (a.options ?? []).find((o) => o.kind === (d.approve ? 'approve' : 'deny') && !/always/i.test(o.id + o.label));
      client.dispatch(chat, d.approve
        ? { type: 'chat/toolCallConfirmed', turnId: a.turnId, toolCallId: a.toolCallId, approved: true, confirmed: 'user-action', ...(option ? { selectedOptionId: option.id } : {}) }
        : { type: 'chat/toolCallConfirmed', turnId: a.turnId, toolCallId: a.toolCallId, approved: false, reason: 'denied', ...(option ? { selectedOptionId: option.id } : {}) });
    }
  }, 100);
  return () => clearInterval(tick);
}
