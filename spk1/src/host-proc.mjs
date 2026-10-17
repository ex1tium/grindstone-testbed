// The host as its own process (experiment 8): session records come from a JSON file the control side writes.
import { readFileSync } from 'node:fs';
import { startHost, SESSIONS } from './host.mjs';

const recordsFile = process.env.SPK1_RECORDS;
const reload = () => { for (const [id, r] of Object.entries(JSON.parse(readFileSync(recordsFile, 'utf8')))) SESSIONS.set(id, { ...r, claudeToken: process.env.GRINDSTONE_CLAUDE_OAUTH_TOKEN }); };
const original = SESSIONS.get.bind(SESSIONS);
SESSIONS.get = (id) => { reload(); return original(id); };
await startHost({ root: process.env.SPK1_ROOT, port: Number(process.env.SPK1_PORT), controlToken: process.env.SPK1_CONTROL_TOKEN });
console.log('ready');
