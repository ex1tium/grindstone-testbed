// Start-up self-test (ADR-18): the session must run on the operator's subscription token and nothing
// that outranks it, reroutes it or can inject a policy. Sources: code.claude.com/docs/en/authentication
// ("Authentication precedence"), /env-vars and /managed-settings, read 17 October 2026 (Claude Code 2.1.302).
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

/** Outrank CLAUDE_CODE_OAUTH_TOKEN (precedence tiers 1-4) or select another provider. */
export const OUTRANKING_ENV = [
  'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_USE_ANTHROPIC_AWS',
  'CLAUDE_CODE_USE_MANTLE', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY',
];
/** Credentials, routes and switches for other providers or gateways; none belongs in a session. */
export const FOREIGN_ENV = [
  'AWS_BEARER_TOKEN_BEDROCK', 'ANTHROPIC_FOUNDRY_API_KEY', 'ANTHROPIC_FOUNDRY_AUTH_TOKEN', 'ANTHROPIC_AWS_API_KEY',
  'ANTHROPIC_BASE_URL', 'ANTHROPIC_AWS_BASE_URL', 'ANTHROPIC_BEDROCK_BASE_URL', 'ANTHROPIC_BEDROCK_MANTLE_BASE_URL',
  'ANTHROPIC_FOUNDRY_BASE_URL', 'ANTHROPIC_VERTEX_BASE_URL', 'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST',
  'CLAUDE_CODE_SKIP_BEDROCK_AUTH', 'CLAUDE_CODE_SKIP_VERTEX_AUTH', 'CLAUDE_CODE_SKIP_FOUNDRY_AUTH',
  'CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH', 'CLAUDE_CODE_SKIP_MANTLE_AUTH',
  'ANTHROPIC_PROFILE', 'ANTHROPIC_FEDERATION_RULE_ID', 'ANTHROPIC_ORGANIZATION_ID', 'CLAUDE_CODE_OAUTH_REFRESH_TOKEN',
];
/** Settings keys that supply or redirect a credential (tier 4, the gateway, or an env block). */
const SETTINGS_KEYS = ['apiKeyHelper', 'env', 'forceLoginMethod', 'forceLoginGatewayUrl', 'forceLoginOrgUUID', 'allowedProviders'];

export function selfTest(env, { managedDir = env.GRINDSTONE_MANAGED_DIR ?? '/etc/claude-code' } = {}) {
  const problems = [];
  if (!env.CLAUDE_CODE_OAUTH_TOKEN) problems.push('CLAUDE_CODE_OAUTH_TOKEN is not set');
  for (const name of [...OUTRANKING_ENV, ...FOREIGN_ENV]) if (env[name]) problems.push(`${name} is set`);
  // Managed settings apply to Agent SDK sessions whatever settingSources says.
  for (const f of ['managed-settings.json', 'managed-settings.d', 'managed-mcp.json']) {
    if (existsSync(join(managedDir, f))) problems.push(`managed policy present: ${join(managedDir, f)}`);
  }
  // The session's private configuration directory: Grindstone writes it; nothing in it may carry a credential.
  if (!env.CLAUDE_CONFIG_DIR) problems.push('CLAUDE_CONFIG_DIR is not set (each session needs its own)');
  else {
    if (existsSync(join(env.CLAUDE_CONFIG_DIR, '.credentials.json'))) problems.push('a stored login is in CLAUDE_CONFIG_DIR');
    const settings = join(env.CLAUDE_CONFIG_DIR, 'settings.json');
    if (existsSync(settings)) {
      const found = Object.keys(JSON.parse(readFileSync(settings, 'utf8'))).filter((k) => SETTINGS_KEYS.includes(k));
      if (found.length) problems.push(`CLAUDE_CONFIG_DIR/settings.json sets ${found.join(', ')}`);
    }
  }
  return problems;
}
