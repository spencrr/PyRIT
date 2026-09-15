import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { TreeContinuation, TreeWorkspace } from '@/types'
import { discoverTreeContinuation } from '@/components/ConversationTree/treeHistory'
import { applyTreeCommand, createTreeWorkspace } from '@/components/ConversationTree/treeModel'

import { useTreeContinuation } from './useTreeContinuation'

jest.mock('@/components/ConversationTree/treeHistory', () => ({ discoverTreeContinuation: jest.fn() }))

function Consumer({ tree, active = true }: { tree: TreeWorkspace; active?: boolean }) {
  const history = useTreeContinuation(tree, tree.nodes[0].id, active, false)
  return <><button onClick={history.refresh}>Refresh history</button>
    <span>{history.continuation ? `${history.continuation.nodes.length} additions` : 'No discovery'}</span>
    {history.error && <span role="alert">{history.error}</span>}
  </>
}
function fixture(): TreeWorkspace {
  const tree = applyTreeCommand(createTreeWorkspace({
    name: 'History', targetRegistryName: 'target', targetIdentifierHash: 'hash', labels: {}, systemPrompt: '',
  }), { type: 'add', parentId: null, prompt: 'Earlier prompt' })
  tree.nodes[0] = { ...tree.nodes[0], conversationId: 'conversation', attackResultId: 'attack', status: 'completed', attemptId: 'attempt' }
  return tree
}

describe('useTreeContinuation', () => {
  beforeEach(() => { jest.clearAllMocks() })
  it('refreshes on activation and user request, not on presentation updates', async () => {
    const user = userEvent.setup()
    const tree = fixture()
    jest.mocked(discoverTreeContinuation).mockResolvedValue({ workspaceId: tree.id, nodeId: tree.nodes[0].id, attemptId: 'attempt', nodes: [], pendingMessages: 0 })
    const view = render(<Consumer tree={tree} />)
    expect(await screen.findByText('0 additions')).toBeVisible()
    view.rerender(<Consumer tree={{ ...tree, nodes: [{ ...tree.nodes[0], position: { x: 40, y: 50 } }] }} />)
    expect(discoverTreeContinuation).toHaveBeenCalledTimes(1)
    view.rerender(<Consumer tree={tree} active={false} />)
    view.rerender(<Consumer tree={tree} />)
    await waitFor(() => expect(discoverTreeContinuation).toHaveBeenCalledTimes(2))
    await user.click(screen.getByRole('button', { name: 'Refresh history' }))
    await waitFor(() => expect(discoverTreeContinuation).toHaveBeenCalledTimes(3))
  })
  it('does not publish history fetched for a prior attempt', async () => {
    const tree = fixture()
    let finish: ((result: TreeContinuation) => void) | undefined
    jest.mocked(discoverTreeContinuation).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    jest.mocked(discoverTreeContinuation).mockRejectedValueOnce(new Error('Backend unavailable'))
    const view = render(<Consumer tree={tree} />)
    view.rerender(<Consumer tree={{ ...tree, nodes: [{ ...tree.nodes[0], attemptId: 'new' }] }} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Backend unavailable')
    await act(async () => { finish?.({ workspaceId: tree.id, nodeId: tree.nodes[0].id, attemptId: 'attempt', nodes: [tree.nodes[0]], pendingMessages: 0 }) })
    expect(screen.queryByText('1 additions')).not.toBeInTheDocument()
  })
})
