import path from 'node:path'
import process from 'node:process'

import { defineConfig, devices } from '@playwright/test'

const BACKEND_PORT = Number(process.env.PYRIT_E2E_BACKEND_PORT ?? '18242')
const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT ?? '18244')
for (const port of [BACKEND_PORT, FRONTEND_PORT]) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid assistant test port.')
}
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`
const FRONTEND_URL = `http://127.0.0.1:${FRONTEND_PORT}`
const PYTHON = process.env.PYRIT_E2E_PYTHON ?? 'python'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'tree-assistant.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 45_000,
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: FRONTEND_URL,
    screenshot: 'only-on-failure',
    storageState: { cookies: [], origins: [{ origin: FRONTEND_URL, localStorage: [{ name: 'pyrit-tour-completed', value: 'true' }] }] },
  },
  webServer: [
    {
      command: `"${PYTHON}" "${path.resolve('..', 'tests', 'end_to_end', 'tree_assistant_server.py')}"`,
      url: `${BACKEND_URL}/api/health`, reuseExistingServer: false, timeout: 120_000,
      env: {
        PYRIT_DEV_MODE: 'true', PYTHONIOENCODING: 'utf-8', PYRIT_E2E_BACKEND_PORT: String(BACKEND_PORT),
        PYRIT_CONFIG_FILE: path.resolve('..', 'tests', 'end_to_end', 'tree_assistant_config.yaml'),
        PYRIT_TREE_ASSISTANT_MODEL: 'local-assistant', PYRIT_TREE_ASSISTANT_API_KEY: 'local-test-only',
        PYRIT_TREE_ASSISTANT_BASE_URL: `${BACKEND_URL}/e2e-model/v1`,
        ENTRA_TENANT_ID: '', ENTRA_CLIENT_ID: '', ENTRA_ALLOWED_GROUP_IDS: '',
      },
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${FRONTEND_PORT} --strictPort`,
      url: FRONTEND_URL, reuseExistingServer: false, timeout: 120_000,
      env: { PYRIT_BACKEND_URL: BACKEND_URL, E2E_FRONTEND_PORT: String(FRONTEND_PORT) },
    },
  ],
})
