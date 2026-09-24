import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { convertersApi, scorersApi, targetsApi } from '@/services/api'
import type { TreeAssistantApply, TreeAssistantPreparedProposal, TreeAssistantProposal, TreeAssistantReceipt, TreeWorkspace } from '@/types'

import ConversationTree from './ConversationTree'
import { runTree, TreePersistenceError } from './treeExecution'
import { applyTreeCommand, createTreeWorkspace, getTreeSettings } from './treeModel'
import { listTreeWorkspaces, saveTreeWorkspace } from './treeStorage'
import { discoverTreeContinuation } from './treeHistory'
import { prepareAssistantProposal } from './treeAssistant'

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
let mockAssistantProposal: TreeAssistantProposal | null = null
let mockAssistantReview: TreeAssistantPreparedProposal | undefined
let mockAssistantResults: TreeAssistantReceipt[] = []
jest.mock('./TreeAssistantPanel', () => ({
  __esModule: true,
  default: ({ onApply, disabled }: { onApply: TreeAssistantApply; disabled: boolean }) =>
    <button disabled={disabled} onClick={() => {
      if (mockAssistantProposal) void onApply(mockAssistantProposal, undefined, mockAssistantReview).then((result) => { mockAssistantResults.push(result) })
    }}>Approve assistant test proposal</button>,
}))

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
    mockAssistantProposal = null
    mockAssistantReview = undefined
    mockAssistantResults = []
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
    await user.click(await screen.findByRole('checkbox', { name: "Don't ask again in this workspace" }))
    await user.click(screen.getByRole('button', { name: 'Run approved drafts' }))
    await waitFor(() => expect(runTree).toHaveBeenCalledTimes(1))
    const settings = getTreeSettings(jest.mocked(runTree).mock.calls[0][0])
    expect(settings.confirmRuns).toBe(false)
    expect(settings.autoRun).toBe(false)
    expect(await screen.findByRole('button', { name: 'Run drafts', exact: true })).toBeEnabled()
  })

  it('uses Workspace naming, hides branches and enables both detail panes by default', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    expect(screen.getByRole('heading', { name: 'Workspace', exact: true })).toBeVisible()
    expect(screen.queryByRole('complementary', { name: 'Tree outline' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Branches', exact: true })).toHaveAttribute('aria-pressed', 'false')
    await user.click(screen.getByRole('button', { name: 'Workspace options' }))
    expect(screen.getByRole('menuitem', { name: 'Use one detail pane' })).toBeInTheDocument()
  })

  it('adds the first draft to an objective-only workspace without losing its objective', async () => {
    const user = userEvent.setup()
    const tree = createTreeWorkspace({ name: 'Empty', targetRegistryName: 'local', targetIdentifierHash: 'hash', systemPrompt: '', labels: {} })
    tree.settings = { ...getTreeSettings(tree), objective: 'Evaluate grounding' }
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.type(screen.getByLabelText('First prompt'), 'Start with a harmless question')
    expect(screen.getByRole('button', { name: 'Plan with assistant' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Add first prompt' }))
    expect(await screen.findByLabelText('Prompt')).toHaveValue('Start with a harmless question')
    expect(jest.mocked(saveTreeWorkspace).mock.calls[0][0].settings?.objective).toBe('Evaluate grounding')
    expect(runTree).not.toHaveBeenCalled()
  })

  it('runs only newly added agent drafts once when workspace auto-run is enabled', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), autoRun: true }
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    mockAssistantProposal = {
      id: 'auto-add', workspace_id: tree.id, base_revision: tree.revision, status: 'pending', summary: 'Another root',
      action: { kind: 'mutate', commands: [{ type: 'add', parentId: null, prompt: 'Only new draft' }] },
    }
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults).toHaveLength(1))
    expect(runTree).toHaveBeenCalledTimes(1)
    const [candidate, options] = jest.mocked(runTree).mock.calls[0]
    expect(options.nodeIds).toEqual([candidate.nodes[1].id])
    expect(options.nodeIds).not.toContain(tree.nodes[0].id)
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults).toHaveLength(2))
    expect(runTree).toHaveBeenCalledTimes(1)
    expect(saveTreeWorkspace).toHaveBeenCalledTimes(1)
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
    await user.click(screen.getByRole('button', { name: 'Branches', exact: true }))
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
    await user.click(await screen.findByRole('button', { name: 'Branches', exact: true }))
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
    await user.click(screen.getByRole('button', { name: 'Branches', exact: true }))
    expect(within(screen.getByRole('complementary', { name: 'Tree outline' })).getAllByRole('button', { name: 'One batch (draft)' })).toHaveLength(1)
  })
  it('undoes a local draft edit and redoes it through persistence', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.clear(screen.getByLabelText('Prompt'))
    await user.type(screen.getByLabelText('Prompt'), 'Reversible edit')
    await user.click(screen.getByRole('button', { name: 'Save draft' }))
    await user.click(screen.getByRole('button', { name: 'Workspace options' }))
    await user.click(screen.getByRole('menuitem', { name: 'Undo edit' }))
    expect(screen.getByLabelText('Prompt')).toHaveValue('Describe your limitations.')
    await user.click(screen.getByRole('button', { name: 'Workspace options' }))
    await user.click(screen.getByRole('menuitem', { name: 'Redo edit' }))
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
    await user.click(screen.getByRole('button', { name: 'Workspace options' }))
    await user.click(screen.getByRole('menuitem', { name: 'Score existing responses', exact: true }))
    expect(await screen.findByText('No eligible nodes in this selection.')).toBeVisible()
    expect(scorersApi.score).not.toHaveBeenCalled()
  })

  it('honors explicit draft-only assistant edits even when auto-run is enabled', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), autoRun: true, confirmRuns: false }
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    mockAssistantProposal = { id: 'assistant-add', workspace_id: tree.id, base_revision: tree.revision, summary: 'Add child', status: 'pending',
      action: { kind: 'mutate', run: false, commands: [{ type: 'add', parentId: tree.nodes[0].id, prompt: 'Agent-proposed child', converters: [] }] } }
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults).toHaveLength(1))
    expect(mockAssistantResults[0]).toMatchObject({ status: 'applied', revision: 1, detail: expect.stringContaining('No model calls') })
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults).toHaveLength(2))
    expect(saveTreeWorkspace).toHaveBeenCalledTimes(1)
    expect(runTree).not.toHaveBeenCalled()
    expect(jest.mocked(saveTreeWorkspace).mock.calls[0][0].nodes[1]).toMatchObject({ prompt: 'Agent-proposed child', status: 'draft' })
  })

  it.each([false, true])('atomically adds a multi-level plan and runs only when approved (run=%s)', async (run) => {
    const user = userEvent.setup()
    const tree = fixture()
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    mockAssistantProposal = {
      id: 'multi-level-plan', workspace_id: tree.id, base_revision: tree.revision, summary: 'Plan chain', status: 'pending',
      action: { kind: 'plan', run, steps: [
        { id: 'first', parent: null, prompt: 'First', converters: [] },
        { id: 'second', parent: { step_id: 'first' }, prompt: 'Second', converters: [] },
      ] },
    }
    mockAssistantReview = prepareAssistantProposal(tree, mockAssistantProposal)
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults).toHaveLength(1))
    const saved = jest.mocked(saveTreeWorkspace).mock.calls[0][0]
    expect(saved.nodes).toHaveLength(3)
    expect(saved.nodes[2].parentId).toBe(saved.nodes[1].id)
    expect(saved.nodes.slice(1).map((node) => node.id)).toEqual(mockAssistantReview.addedNodeIds)
    expect(saveTreeWorkspace).toHaveBeenCalledTimes(1)
    if (run) expect(jest.mocked(runTree).mock.calls[0][1].nodeIds).toEqual(saved.nodes.slice(1).map((node) => node.id))
    else expect(runTree).not.toHaveBeenCalled()
  })

  it('locks the editor only while an approved mutation is being saved', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    mockAssistantProposal = {
      id: 'delayed-add', workspace_id: tree.id, base_revision: tree.revision, summary: 'Add child', status: 'pending',
      action: { kind: 'mutate', commands: [{ type: 'add', parentId: tree.nodes[0].id, prompt: 'Agent child' }] },
    }
    let finish: (() => void) | undefined
    jest.mocked(saveTreeWorkspace).mockImplementationOnce((candidate) => new Promise((resolve) => {
      finish = () => { resolve({ ...candidate, revision: candidate.revision + 1 }) }
    }))
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    expect(screen.getByLabelText('Prompt')).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(finish).toBeDefined())
    expect(screen.getByLabelText('Prompt')).toBeDisabled()
    expect(screen.getByLabelText('Node size')).toBeDisabled()
    await user.type(screen.getByLabelText('Prompt'), 'Unsaved human work')
    expect(screen.getByLabelText('Prompt')).toHaveValue('Describe your limitations.')
    await act(async () => { finish?.() })
    expect(screen.getByLabelText('Prompt')).toBeEnabled()
    expect(screen.getByLabelText('Prompt')).toHaveValue('Agent child')
    expect(screen.getByRole('combobox', { name: 'Workspace', exact: true })).toBeEnabled()
    expect(mockAssistantResults[0]?.status).toBe('applied')
  })

  it('applies reviewed node IDs to the latest layout without losing presentation edits', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    mockAssistantProposal = {
      id: 'reviewed-add', workspace_id: tree.id, base_revision: tree.revision, summary: 'Add child', status: 'pending',
      action: { kind: 'mutate', commands: [{ type: 'add', parentId: tree.nodes[0].id, prompt: 'Reviewed child' }] },
    }
    mockAssistantReview = prepareAssistantProposal(tree, mockAssistantProposal)
    const moved = { ...applyTreeCommand(tree, { type: 'move', nodeId: tree.nodes[0].id, position: { x: 45, y: 90 } }), revision: tree.revision + 1 }
    jest.mocked(listTreeWorkspaces).mockReturnValue([moved])
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults).toHaveLength(1))
    expect(mockAssistantResults[0].status).toBe('applied')
    const saved = jest.mocked(saveTreeWorkspace).mock.calls[0][0]
    expect(saved.revision).toBe(moved.revision)
    expect(saved.nodes[0].position).toEqual({ x: 45, y: 90 })
    expect(saved.nodes[1].id).toBe(mockAssistantReview.addedNodeIds[0])
  })

  it.each(['breadth-first', 'depth-first'] as const)('runs branching plans in canonical %s order', async (traversal) => {
    const user = userEvent.setup()
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), traversal }
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    const first = { id: 'first', parent: null, prompt: 'First', converters: [] }
    const nested = { id: 'nested', parent: { step_id: 'first' }, prompt: 'Nested', converters: [] }
    const other = { id: 'other', parent: null, prompt: 'Other', converters: [] }
    mockAssistantProposal = {
      id: 'branching-plan', workspace_id: tree.id, base_revision: tree.revision, summary: 'Branch', status: 'pending',
      action: { kind: 'plan', run: true, steps: traversal === 'breadth-first' ? [first, nested, other] : [first, other, nested] },
    }
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(runTree).toHaveBeenCalledTimes(1))
    const [saved, options] = jest.mocked(runTree).mock.calls[0]
    expect(options.nodeIds.map((id) => saved.nodes.find((node) => node.id === id)?.prompt))
      .toEqual(traversal === 'breadth-first' ? ['First', 'Other', 'Nested'] : ['First', 'Nested', 'Other'])
  })

  it.each([
    { kind: 'score' as const, scoreStatus: 'not_applicable' as const },
    { kind: 'run' as const, scoreStatus: 'not_applicable' as const },
    { kind: 'score' as const, scoreStatus: 'complete' as const },
    { kind: 'run' as const, scoreStatus: 'complete' as const },
  ])('reports $scoreStatus results accurately for assistant $kind approvals', async ({ kind, scoreStatus }) => {
    const user = userEvent.setup()
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), autoScore: true, scorers: [{
      scorer_id: 'judge', scorer_type: 'Scale', identifier_hash: 'hash', score_type: 'float_scale', scope: 'response', highIsRisk: true,
    }] }
    const completed: TreeWorkspace['nodes'][number] = {
      ...tree.nodes[0], status: 'completed', attackResultId: 'attack', conversationId: 'conversation', lastSequence: 1,
      messages: ['user', 'assistant'].map((role, turn_number) => ({
        role, turn_number, created_at: '2026-09-17T00:00:00.000Z', message_pieces: [{
          id: `piece-${turn_number}`, original_value_data_type: 'text', converted_value_data_type: 'text',
          original_value: role === 'user' ? tree.nodes[0].prompt : 'Recorded reply',
          converted_value: role === 'user' ? tree.nodes[0].prompt : 'Recorded reply',
          response_error: 'none', scores: [],
        }],
      })),
    }
    if (kind === 'score') tree.nodes[0] = completed
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    mockAssistantProposal = {
      id: 'score-result', workspace_id: tree.id, base_revision: tree.revision, summary: 'Evaluate', status: 'pending',
      action: { kind, node_ids: [tree.nodes[0].id] },
    }
    jest.mocked(scorersApi.score).mockResolvedValue({
      scorer_id: 'judge', scorer_hash: 'hash', status: scoreStatus,
      scores: scoreStatus === 'complete' ? [{
        id: 'score', message_piece_id: 'piece-1', scorer_type: 'Scale', score_type: 'float_scale',
        score_value: '0.8', status: 'complete', timestamp: '2026-09-17T00:00:00.000Z',
      }] : [],
    })
    jest.mocked(runTree).mockImplementation(async (snapshot, options) => {
      if (!options.commitNodeUpdate || !options.getLatest || !options.onNodeCompleted) throw new Error('Missing controller integration')
      await options.commitNodeUpdate(completed.id, snapshot.nodes[0], completed)
      await options.onNodeCompleted(options.getLatest(), completed.id)
      return options.getLatest()
    })
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults).toHaveLength(1))
    expect(mockAssistantResults[0].status).toBe(scoreStatus === 'complete' ? 'applied' : 'failed')
    expect(mockAssistantResults[0].detail).toContain(scoreStatus === 'complete' ? '1/1' : '0/1')
    if (scoreStatus === 'not_applicable') expect(mockAssistantResults[0].detail).toContain('1 scorer evaluations not applicable (no scores produced)')
    const snapshots = jest.mocked(saveTreeWorkspace).mock.calls
    expect(snapshots[snapshots.length - 1][0].nodes[0].scoreRuns?.[0].status).toBe(scoreStatus)
  })

  it('rejects assistant proposals after a human edit without overwriting it', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    mockAssistantProposal = { id: 'stale-edit', workspace_id: tree.id, base_revision: tree.revision, summary: 'Edit root', status: 'pending',
      action: { kind: 'mutate', commands: [{ type: 'edit', nodeId: tree.nodes[0].id, prompt: 'Agent edit', converters: [] }] } }
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.clear(screen.getByLabelText('Prompt'))
    await user.type(screen.getByLabelText('Prompt'), 'Human edit')
    expect(screen.getByRole('button', { name: 'Approve assistant test proposal' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Save draft' }))
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults).toHaveLength(1))
    expect(mockAssistantResults[0]).toMatchObject({ status: 'failed', detail: expect.stringContaining('tree changed') })
    expect(screen.getByLabelText('Prompt')).toHaveValue('Human edit')
    expect(saveTreeWorkspace).toHaveBeenCalledTimes(1)
  })

  it('approves retry edits without sending and retains the archived attempt', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    tree.nodes[0] = { ...tree.nodes[0], status: 'error', error: 'Prior failure' }
    tree.settings = { ...getTreeSettings(tree), autoRun: true, confirmRuns: false }
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    mockAssistantProposal = { id: 'retry', workspace_id: tree.id, base_revision: tree.revision, summary: 'Retry preparation', status: 'pending',
      action: { kind: 'mutate', commands: [{ type: 'retry', nodeId: tree.nodes[0].id, scope: 'node' }] } }
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults[0]?.status).toBe('applied'))
    expect(runTree).not.toHaveBeenCalled()
    expect(jest.mocked(saveTreeWorkspace).mock.calls[0][0].nodes[0]).toMatchObject({ status: 'draft', attempts: [expect.objectContaining({ error: 'Prior failure' })] })
  })

  it('reports failed execution honestly and dispatches only the approved node scope', async () => {
    const user = userEvent.setup()
    const root = fixture()
    const tree = applyTreeCommand(root, { type: 'add', parentId: null, prompt: 'Not approved' })
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    mockAssistantProposal = { id: 'run', workspace_id: tree.id, base_revision: tree.revision, summary: 'Run one', status: 'pending',
      action: { kind: 'run', node_ids: [tree.nodes[0].id] } }
    jest.mocked(runTree).mockImplementation(async (workspace, options) => {
      const node = workspace.nodes[0]
      if (!options.commitNodeUpdate || !options.getLatest) throw new Error('Missing controller integration')
      await options.commitNodeUpdate(node.id, node, { status: 'error', error: 'Target failed' })
      return options.getLatest()
    })
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults[0]?.status).toBe('failed'))
    expect(mockAssistantResults[0].detail).toContain('0/1 nodes completed')
    expect(jest.mocked(runTree).mock.calls[0][1].nodeIds).toEqual([tree.nodes[0].id])
    expect(jest.mocked(saveTreeWorkspace).mock.calls[0][0].nodes[1].status).toBe('draft')
  })

  it('keeps the last run outcome unchanged after preparing a retry', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    jest.mocked(listTreeWorkspaces).mockReturnValue([tree])
    jest.mocked(runTree).mockImplementation(async (workspace, options) => {
      const node = workspace.nodes[0]
      if (!options.commitNodeUpdate || !options.getLatest) throw new Error('Missing controller integration')
      await options.commitNodeUpdate(node.id, node, { status: 'error', error: 'Original failure' })
      return options.getLatest()
    })
    render(<TestWrapper><ConversationTree activeTarget={null} labels={{}} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Review & run drafts' }))
    await user.click(await screen.findByRole('button', { name: 'Run approved drafts' }))
    await screen.findByText(/0\/1 nodes completed/)
    await user.click(await screen.findByRole('button', { name: 'Activity', exact: true }))
    const previous = screen.getByRole('region', { name: 'Run activity' }).textContent
    const latest = jest.mocked(saveTreeWorkspace).mock.results[0].value
    const saved: TreeWorkspace = await latest
    mockAssistantProposal = {
      id: 'retry-preparation', workspace_id: tree.id, base_revision: saved.revision, status: 'pending',
      summary: 'Prepare a retry', action: { kind: 'mutate', commands: [{ type: 'retry', nodeId: tree.nodes[0].id, scope: 'node' }] },
    }
    await user.click(screen.getByRole('button', { name: 'Approve assistant test proposal' }))
    await waitFor(() => expect(mockAssistantResults[0]?.status).toBe('applied'))
    expect(screen.getByRole('region', { name: 'Run activity' }).textContent).toBe(previous)
    expect(runTree).toHaveBeenCalledTimes(1)
  })
})
