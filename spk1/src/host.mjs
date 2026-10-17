// SPK-1 host: @ahpd/sdk with one backend, Grindstone's wrapper around @ahpd/agent-acp, which runs the launcher
// (claude-agent-acp behind Grindstone's options and self-test). Per session, the wrapper adds that session's
// kernel MCP server and bearer token, read from a table the control side fills (never from session config).
import { createHost, listen } from '@ahpd/sdk';
import { acpAgent } from '@ahpd/agent-acp';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { createHmac, randomUUID } from 'node:crypto';

const LAUNCHER = new URL('./launcher.mjs', import.meta.url).pathname;

/** Session UUID -> what Grindstone gives that session. Filled by the control side before createSession.
 * Keyed by the UUID: the client names `ahp-session:/<uuid>`, the backend sees `<provider>:/<uuid>`. */
export const SESSIONS = new Map();

function agentEnv(session) {
  // Only what the agent needs: never the host's environment (agent-acp merges env over the host's own,
  // so the host process itself runs with a minimal environment too).
  return {
    PATH: process.env.PATH,
    HOME: session.home,
    CLAUDE_CONFIG_DIR: session.configDir,
    CLAUDE_CODE_OAUTH_TOKEN: session.claudeToken,
    GRINDSTONE_ACP_OPTIONS: JSON.stringify(session.options ?? {}),
    ...(session.timingLog ? { SPK1_TIMING_LOG: session.timingLog } : {}),
    // A TLS-intercepting egress (the cloud spike environment) needs its CA; production sets none.
    ...(process.env.NODE_EXTRA_CA_CERTS ? { NODE_EXTRA_CA_CERTS: process.env.NODE_EXTRA_CA_CERTS } : {}),
  };
}

export const sessionId = (uri) => uri.slice(uri.lastIndexOf('/') + 1);

export function grindstoneClaude() {
  const describe = acpAgent({ provider: 'claude', displayName: 'Claude (Grindstone)', command: process.execPath, args: [LAUNCHER], hostTools: false, honoursTrust: true });
  return {
    ...describe,
    create(start) {
      const session = SESSIONS.get(sessionId(start.uri));
      if (!session) throw new Error(`no Grindstone session record for ${start.uri}`);
      // As another Unix user when the session names one: setpriv switches uid and gid (the host needs
      // CAP_SETUID and CAP_SETGID for that) and drops every capability before the agent runs.
      const [command, args] = session.unixUser
        ? ['setpriv', [`--reuid=${session.unixUser}`, `--regid=${session.unixUser}`, '--init-groups', '--inh-caps=-all', '--ambient-caps=-all', process.execPath, LAUNCHER]]
        : [process.execPath, [LAUNCHER]];
      const inner = acpAgent({
        provider: 'claude', displayName: 'Claude (Grindstone)', command, args,
        env: agentEnv(session), hostTools: false, honoursTrust: true, log: (line) => session.log?.(line),
      });
      const mcpServers = { ...(start.mcpServers ?? {}) };
      if (session.mcp) {
        mcpServers.grindstone = { type: 'http', url: session.mcp.url, headers: { Authorization: `Bearer ${session.mcp.token}` } };
      }
      return inner.create({ ...start, mcpServers });
    },
  };
}

/**
 * Signs every action the host sends to the control side (experiment 9): an event ID, the channel's turn number,
 * and an HMAC-SHA256 over both and the action envelope, under a key only the host and the control plane hold.
 */
export function signer(key) {
  const turns = new Map();
  return (params) => {
    const turn = (turns.get(params.channel) ?? 0) + (params.action?.type === 'chat/turnStarted' ? 1 : 0);
    turns.set(params.channel, turn);
    const sig = { eventId: randomUUID(), turn, at: Date.now() };
    return { ...params, _grindstone: { ...sig, mac: macOf(key, params, sig) } };
  };
}
export const macOf = (key, params, sig) => createHmac('sha256', key)
  .update(JSON.stringify([sig.eventId, sig.turn, sig.at, params.channel, params.serverSeq, params.action])).digest('base64url');

export async function startHost({ root, port, controlToken, viewers = new Map(), tap, eventKey }) {
  const sign = eventKey ? signer(eventKey) : null;
  mkdirSync(root, { recursive: true });
  // A people directory turns the host's grant checks on: without one, every connection past the token may
  // dispatch anything (@ahpd/sdk 0.12.0, host/actions.js). Viewers resolve through it; the control side is root.
  const users = { resource: {}, verify: async (token) => viewers.get(token) };
  const host = createHost({ path: root, agents: [grindstoneClaude()], ...(viewers.size > 0 && !process.env.SPK1_NO_USERS ? { users } : {}), onEvent: (line) => process.env.SPK1_HOST_LOG && console.error('[host]', line) });
  const listener = await listen(
    {
      port, token: controlToken, root: true, tap,
      // Any other token is a viewer's: it opens the door as nobody in particular.
      identify: (token) => (viewers.has(token) ? { principal: viewers.get(token) } : undefined),
    },
    (peer, principal, isRoot) => host.accept(isRoot && sign
      ? { ...peer, send: (m) => peer.send(m), request: (...a) => peer.request(...a), close: (...a) => peer.close?.(...a),
          notify: (method, params) => peer.notify(method, method === 'action' ? sign(params) : params) }
      : peer, principal, isRoot),
  );
  return { host, listener };
}

export const workspaceFor = (root, name) => { const d = join(root, name); mkdirSync(d, { recursive: true }); return d; };
