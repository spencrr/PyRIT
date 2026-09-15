import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { convertersApi, scorersApi, targetsApi } from '@/services/api'
import type { TreeWorkspace } from '@/types'

import ConversationTree from './ConversationTree'
import { runTree, TreePersistenceError } from './treeExecution'
import { applyTreeCommand, createTreeWorkspace, getTreeSettings } from './treeModel'
import { listTreeWorkspaces, saveTreeWorkspace } from './treeStorage'
import { discoverTreeContinuation } from './treeHistory'

jest.mock('@/services/api', () => ({
  targetsApi: { listTargets: jest.fn() },
  convertersApi: { listConverterCatalog: jest.fn() },
  scorersApi: { listCatalog: jest.fn(), listScorers: jest.fn(), score: jest.fn() },
}))
jest.mock('./treeExecution', () => ({
  ...jest.requireActual('./treeExecution'), runTree: jest.fn(), recoverTreeNode: jest.fn(),
}))
jest.mock('./treeStorage', () => ({
  listTreeWorkspaces: jest.fn(), loadTreeWorkspace: jest.fn(), saveTreeWorkspace: jest.fn(), deleteTreeWorkspace: jest.fn(),
  TREE_STORAGE_PREFIX: 'pyrit:conversation-tree:v1:',
}))
jest.mock('./TreeCanvas', () => ({ __esModule: true, default: () => <section aria-label="Conversation graph" /> }))
jest.mock('./treeHistory', () => ({ discoverTreeContinuation: jest.fn() }))

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}
function fixture(): TreeWorkspace {
  return applyTreeCommand(createTreeWorkspace({
    name: 'Evaluation', targetRegistryName: 'local', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
  }), { type: 'add', parentId: null, prompt: 'Describe your limitations.' })
}

describe('ConversationTree', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(targetsApi.listTargets).mockResolvedValue({ items: [], pagination: { limit: 100, has_more: false } })
    jest.mocked(convertersApi.listConverterCatalog).mockResolvedValue({ items: [] })
    jest.mocked(listTreeWorkspaces).mockReturnValue([fixture()])
    jest.mocked(saveTreeWorkspace).mockImplementation(async (workspace: TreeWorkspace) => ({ ...workspace, revision: workspace.revision + 1 }))
    jest.mocked(runTree).mockImplementation(async (workspace: TreeWorkspace) => workspace)
    jest.mocked(discoverTreeContinuation).mockImplementation(async (workspace, nodeId) => ({
      workspaceId: workspace.id, nodeId, attemptId: workspace.nodes.find((node) => node.id === nodeId)?.attemptId ?? '', nodes: [], pendingMessages: 0,
    }))
  })

  it('keeps confirmations independent from auto-run and supports workspace opt-out in the modal', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Review & run drafts' }))
    expect(runTree).not.toHaveBeenCalled()
    await user.click(screen.getByRole('checkbox', { name: "Don't ask again in this workspace" }))
    await user.click(screen.getByRole('button', { name: 'Run approved drafts' }))
    await waitFor(() => expect(runTree).toHaveBeenCalledTimes(1))
    const settings = getTreeSettings(jest.mocked(runTree).mock.calls[0][0])
    expect(settings.confirmRuns).toBe(false)
    expect(settings.autoRun).toBe(false)
    expect(await screen.findByRole('button', { name: 'Run drafts', exact: true })).toBeEnabled()
  })

  it('retains dirty drafts across tab navigation and blocks accidental runs', async () => {
    const user = userEvent.setup()
    const view = render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.clear(screen.getByLabelText('Prompt'))
    await user.type(screen.getByLabelText('Prompt'), 'Retain me')
    view.rerender(<TestWrapper><ConversationTree activeTarget={null} labels={{}} active={false} /></TestWrapper>)
    const leave = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(leave)
    expect(leave.defaultPrevented).toBe(true)
    view.rerender(<TestWrapper><ConversationTree activeTarget={null} labels={{}} active /></TestWrapper>)
    expect(screen.getByLabelText('Prompt')).toHaveValue('Retain me')
    expect(screen.getByRole('button', { name: 'Review & run drafts' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Discard edits' }))
    expect(screen.getByRole('button', { name: 'Review & run drafts' })).toBeEnabled()
  })

  it('persists composed child branches and preserves them after a rejected save', async () => {
    const user = userEvent.setup()
    jest.mocked(saveTreeWorkspace).mockRejectedValueOnce(new Error('Quota exceeded'))
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.type(screen.getByLabelText('Child prompt 1'), 'Keep child')
    await user.click(screen.getByRole('button', { name: 'Add 1 child' }))
    expect(await screen.findByText('Quota exceeded')).toBeVisible()
    expect(screen.getByLabelText('Child prompt 1')).toHaveValue('Keep child')
    await user.click(screen.getByRole('button', { name: 'Add 1 child' }))
    expect(await within(screen.getByRole('complementary', { name: 'Tree outline' }))
      .findByRole('button', { name: 'Keep child (draft)' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('allows an unrelated draft to be edited while a request is in flight and retains both changes', async () => {
    const user = userEvent.setup()
    const tree = applyTreeCommand(fixture(), { type: 'add', parentId: null, prompt: 'Independent branch' })
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    let finish: (() => void) | undefined
    jest.mocked(runTree).mockImplementation(async (snapshot, options) => {
      const node = snapshot.nodes[0]
      if (!options.commitNodeUpdate || !options.getLatest) throw new Error('Missing transactional execution callbacks')
      await options.commitNodeUpdate(node.id, node, { status: 'running' })
      await new Promise<void>((resolve) => { finish = resolve })
      const expected = options.getLatest().nodes[0]
      await options.commitNodeUpdate(node.id, expected, { status: 'error', error: 'Test response failure' })
      return options.getLatest()
    })
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Review & run drafts' }))
    await user.click(screen.getByRole('button', { name: 'Run approved drafts' }))
    await waitFor(() => expect(finish).toBeDefined())
    await user.click(within(screen.getByRole('complementary', { name: 'Tree outline' })).getByRole('button', { name: 'Independent branch (draft)' }))
    expect(screen.getByLabelText('Prompt')).toBeEnabled()
    await user.clear(screen.getByLabelText('Prompt'))
    await user.type(screen.getByLabelText('Prompt'), 'Edited during execution')
    await user.click(screen.getByRole('button', { name: 'Save draft' }))
    await waitFor(() => expect(screen.getByText(/removed from the current queue/)).toBeVisible())
    await act(async () => { finish?.() })
    const saved = jest.mocked(saveTreeWorkspace).mock.calls.map(([snapshot]) => snapshot).pop()
    expect(saved?.nodes[0]).toMatchObject({ status: 'error', error: 'Test response failure' })
    expect(saved?.nodes[1].prompt).toBe('Edited during execution')
  })

  it('offers CAS save-only recovery without another execution', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    jest.mocked(runTree).mockRejectedValue(new TreePersistenceError(tree, new Error('Quota exceeded')))
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Review & run drafts' }))
    await user.click(screen.getByRole('button', { name: 'Run approved drafts' }))
    await user.click(await screen.findByRole('button', { name: 'Retry saving only' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Retry saving only' })).not.toBeInTheDocument())
    expect(runTree).toHaveBeenCalledTimes(1)
  })
  it('locks editing and recovery actions immediately while failed-save requests settle', async () => {
    const user = userEvent.setup()
    let finish: (() => void) | undefined
    jest.mocked(runTree).mockImplementation(async (tree, options) => {
      options.onPersistenceFailure?.(tree)
      await new Promise<void>((resolve) => { finish = resolve })
      throw new TreePersistenceError(tree, new Error('Quota exceeded'))
    })
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Review & run drafts' }))
    await user.click(screen.getByRole('button', { name: 'Run approved drafts' }))
    await waitFor(() => expect(finish).toBeDefined())
    expect(screen.getByRole('button', { name: 'Retry saving only' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Discard unsaved snapshot' })).toBeDisabled()
    expect(screen.getByLabelText('Prompt')).toBeDisabled()
    expect(screen.getByLabelText('Node size')).toBeDisabled()
    await act(async () => { finish?.() })
    expect(screen.getByRole('button', { name: 'Retry saving only' })).toBeEnabled()
  })

  it('allows recorded-result recovery after reload while keeping interrupted input read-only', async () => {
    const tree = fixture()
    tree.nodes[0] = { ...tree.nodes[0], status: 'running', attackResultId: 'attack', conversationId: 'conversation' }
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    expect(await screen.findByRole('button', { name: 'Recover recorded result' })).toBeEnabled()
    expect(screen.getByLabelText('Prompt')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Retry response' })).toBeDisabled()
  })

  it('clears canceled pipeline editing locks without discarding genuine edits', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('tab', { name: 'Pipelines' }))
    const first = within(screen.getByRole('region', { name: 'Pipeline 1' }))
    await user.click(first.getByRole('button', { name: 'Add converter step' }))
    expect(screen.getByRole('button', { name: 'Review & run drafts' })).toBeDisabled()
    await user.click(first.getByRole('button', { name: 'Cancel converter' }))
    expect(screen.getByRole('button', { name: 'Review & run drafts' })).toBeEnabled()
    await user.type(screen.getByLabelText('Shared child prompt'), 'Keep this')
    await user.click(first.getByRole('button', { name: 'Add converter step' }))
    await user.click(first.getByRole('button', { name: 'Cancel converter' }))
    expect(screen.getByRole('button', { name: 'Review & run drafts' })).toBeDisabled()
  })

  it('honors explicit retry approvals even when newly added branches auto-run', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), autoRun: true, confirmRuns: true }
    tree.nodes[0] = { ...tree.nodes[0], status: 'error', error: 'Recorded test failure' }
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Retry response' }))
    expect(await screen.findByRole('heading', { name: 'Approve model calls' })).toBeVisible()
    expect(runTree).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Run approved drafts' }))
    await waitFor(() => expect(runTree).toHaveBeenCalledTimes(1))
  })

  it('accepts only one branch submission while the same save is pending', async () => {
    const user = userEvent.setup()
    let finish: (() => void) | undefined
    jest.mocked(saveTreeWorkspace).mockImplementation((tree) => new Promise((resolve) => {
      finish = () => { resolve({ ...tree, revision: tree.revision + 1 }) }
    }))
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.type(screen.getByLabelText('Child prompt 1'), 'One batch')
    await user.dblClick(screen.getByRole('button', { name: 'Add 1 child' }))
    expect(saveTreeWorkspace).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Add 1 child' })).toBeDisabled()
    await act(async () => { finish?.() })
    expect(within(screen.getByRole('complementary', { name: 'Tree outline' })).getAllByRole('button', { name: 'One batch (draft)' })).toHaveLength(1)
  })
  it('undoes a local draft edit and redoes it through persistence', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.clear(screen.getByLabelText('Prompt'))
    await user.type(screen.getByLabelText('Prompt'), 'Reversible edit')
    await user.click(screen.getByRole('button', { name: 'Save draft' }))
    await user.click(screen.getByRole('button', { name: 'Undo edit' }))
    expect(screen.getByLabelText('Prompt')).toHaveValue('Describe your limitations.')
    await user.click(screen.getByRole('button', { name: 'Redo edit' }))
    expect(screen.getByLabelText('Prompt')).toHaveValue('Reversible edit')
    expect(saveTreeWorkspace).toHaveBeenCalledTimes(3)
  })
  it('scores the exact recorded response rather than the latest backend append', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), confirmRuns: false, scorers: [{
      scorer_id: 'judge', scorer_type: 'Scale', identifier_hash: 'hash', score_type: 'float_scale', scope: 'response', highIsRisk: true,
    }] }
    tree.nodes[0] = { ...tree.nodes[0], status: 'completed', attackResultId: 'attack', conversationId: 'conversation', attemptId: 'attempt', lastSequence: 7,
      messages: [{ turn_number: 6, role: 'user', created_at: '2026-01-01T00:00:00.000Z', message_pieces: [{
        id: 'prompt-piece', original_value_data_type: 'text', converted_value_data_type: 'text',
        original_value: tree.nodes[0].prompt, converted_value: tree.nodes[0].prompt, response_error: 'none', scores: [],
      }] }, { turn_number: 7, role: 'assistant', created_at: '2026-01-01T00:00:00.000Z', message_pieces: [{
        id: 'recorded', original_value_data_type: 'text', converted_value_data_type: 'text', converted_value: 'Earlier evidence', response_error: 'none', scores: [],
      }] }],
    }
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    jest.mocked(scorersApi.score).mockResolvedValue({ scorer_id: 'judge', scorer_hash: 'hash', status: 'complete', scores: [] })
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Score response', exact: true }))
    await waitFor(() => expect(scorersApi.score).toHaveBeenCalledWith('judge', expect.objectContaining({
      evidence_sequence: 7, evidence_message_piece_ids: ['recorded'], expected_response: [{
        id: 'recorded', converted_value: 'Earlier evidence', converted_value_data_type: 'text',
      }],
    })))
    expect(runTree).not.toHaveBeenCalled()
    await waitFor(() => expect(jest.mocked(saveTreeWorkspace).mock.calls.some(([saved]) => saved.nodes[0].scoreRuns?.length === 1)).toBe(true))
  })
  it('does not offer response scoring for a failure that has no assistant evidence', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), confirmRuns: false, scorers: [{
      scorer_id: 'judge', scorer_type: 'Scale', identifier_hash: 'hash', score_type: 'float_scale', scope: 'response', highIsRisk: true,
    }] }
    tree.nodes[0] = { ...tree.nodes[0], status: 'error', error: 'Preparation failed', conversationId: 'conversation', attackResultId: 'attack' }
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Score response', exact: true })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Score existing responses', exact: true }))
    expect(await screen.findByText('No eligible nodes in this selection.')).toBeVisible()
    expect(scorersApi.score).not.toHaveBeenCalled()
  })
})
