// Capture real Co-PyRIT screenshots for the deck using the scripted ACP agent and the local provider.
// Prereqs: backend on 127.0.0.1:8000 (local auth, unauthenticated admin, docker/agent_receipt/pyrit.yaml) and Vite on :3000.
// Usage: node fixtures/real/capture.mjs
import { createRequire } from 'node:module';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = process.env.PYRIT_REPO || 'C:/Users/spencersch/src/PyRIT.worktrees/pyrt-agent-execution-environment';
const { chromium } = createRequire(resolve(repo, 'frontend/package.json'))('playwright');
const API = 'http://127.0.0.1:8000/api';
const UI = 'http://127.0.0.1:3000';
const shot = (name) => resolve(here, name);

// The backend requires the bundled compatibility id; read the expected value from its own problem response.
const probe = await fetch(`${API}/config/agent-presets`);
const compat = (await probe.json()).expected;
const headers = { 'Content-Type': 'application/json', 'PyRIT-Compatibility-ID': compat };

const python = `${repo}/.venv/Scripts/python.exe`.replace(/\//g, '\\');
const agent = `${repo}/tests/integration/agent/scripted_acp_agent.py`.replace(/\//g, '\\');
const configuration = {
  name: 'receipt_agent',
  harness_profile: { harness_kind: 'acp', command: [python, agent], permission_policy: 'ask' },
  environment_template: { environment: 'local', local_execution_acknowledged: true, fixture_directory: 'agent_receipt' },
  artifact_paths: ['receipt.json'],
};
const existing = await (await fetch(`${API}/targets`, { headers })).json();
if (!(existing.items || []).some((t) => t.registry_name === 'receipt_agent' || t.name === 'receipt_agent')) {
  const created = await fetch(`${API}/targets`, { method: 'POST', headers, body: JSON.stringify({ name: 'receipt_agent', type: 'AgentTarget', params: { agent_configuration: configuration } }) });
  console.log('create target', created.status, (await created.text()).slice(0, 300));
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1, colorScheme: 'dark' });
const page = await context.newPage();
page.on('pageerror', (e) => console.error('pageerror', e.message));

// 1. The three-panel create form (admin only), filled for the scripted agent.
await page.goto(`${UI}/registry/targets`);
await page.getByRole('button', { name: 'New Target' }).click();
await page.getByRole('combobox', { name: 'Target Type' }).click();
await page.getByRole('option', { name: /Agent \(ACP\)/ }).click();
await page.getByRole('textbox', { name: 'Target name' }).fill('receipt_agent');
await page.getByRole('combobox', { name: 'Harness', exact: true }).selectOption({ label: 'Custom ACP' });
await page.getByRole('combobox', { name: 'Tool permission policy' }).selectOption({ label: 'Ask me' });
await page.getByRole('textbox', { name: 'Executable' }).fill('python');
await page.getByRole('textbox', { name: 'Arguments' }).fill('scripted_acp_agent.py');
await page.getByRole('combobox', { name: 'Execution environment' }).selectOption({ label: 'Local process' });
await page.getByRole('checkbox', { name: 'I understand local tools can access the host' }).check();
await page.waitForTimeout(400);
await page.screenshot({ path: shot('01-create-form.png') });
await page.getByRole('button', { name: 'Cancel' }).click();

// 2. Make it the default objective target, open chat.
await page.reload();
const combo = page.getByRole('combobox', { name: 'Default objective target' });
await combo.waitFor();
await page.waitForFunction(() => [...document.querySelectorAll('select option')].some((o) => o.textContent.includes('receipt_agent')), null, { timeout: 30000 });
const options = await combo.locator('option').allInnerTexts();
console.log('objective options', options);
await combo.selectOption({ index: options.findIndex((o) => o.includes('receipt_agent')) });
await page.waitForTimeout(800);
await page.goto(`${UI}/chat`);
await page.getByPlaceholder('Type prompt here').waitFor({ timeout: 30000 });
// Sends are blocked while an unregistered Operation label is pending; clear it for the capture.
const banner = page.getByText(/Choose a saved Operation/);
await banner.waitFor({ timeout: 15000 }).catch(() => {});
if (await banner.count()) {
  await page.getByRole('button', { name: 'Clear', exact: true }).first().click();
  await banner.waitFor({ state: 'hidden', timeout: 15000 });
}
await page.waitForTimeout(600);
await page.getByRole('combobox', { name: 'Chat target' }).selectOption('receipt_agent');
await page.waitForTimeout(1500);
await page.screenshot({ path: shot('02-chat-empty.png') });

// 3. Send the prompt that makes the scripted agent ask for permission.
await page.getByPlaceholder('Type prompt here').fill('approve');
await page.getByRole('button', { name: 'Send message', exact: true }).click();
const allow = page.getByRole('button', { name: /Allow once/i }).first();
await allow.waitFor({ timeout: 60000 });
await page.waitForTimeout(700);
await page.screenshot({ path: shot('03-permission-pending.png') });
await allow.click();
await page.getByText(/Receipt total: 31/).first().waitFor({ timeout: 60000 });
await page.waitForTimeout(1500);
await page.screenshot({ path: shot('04-turn-complete.png') });

// 4. A second turn that changes state (31 -> 36).
await page.getByPlaceholder('Type prompt here').fill('Add one orange to the order');
await page.getByRole('button', { name: 'Send message', exact: true }).click();
await page.getByText(/Receipt total: 36/).first().waitFor({ timeout: 60000 });
await page.waitForTimeout(1200);
await page.screenshot({ path: shot('05-second-turn.png') });
const url = page.url();
console.log('chat url', url);

// 5. Administrator view of executions.
await page.goto(`${UI}/registry/executions`);
await page.waitForTimeout(1500);
await page.screenshot({ path: shot('06-executions.png') });

await browser.close();
console.log('captured', here);
