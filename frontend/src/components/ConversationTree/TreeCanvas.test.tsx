import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor } from '@testing-library/react'
import type { Node } from '@xyflow/react'

import TreeCanvas from './TreeCanvas'
import { applyTreeCommand, createTreeWorkspace } from './treeModel'

let mockNodes: Node[] = []
const mockSetViewport = jest.fn().mockResolvedValue(true)
const mockGetNodes = () => mockNodes
const mockSetNodes = (nodes: Node[]) => { mockNodes = nodes }
const mockSetEdges = jest.fn()
const mockGetBounds = jest.fn().mockReturnValue({ x: 0, y: 0, width: 280, height: 240 })
const mockGetViewport = () => ({ x: 20, y: 30, zoom: 0.8 })
jest.mock('@/hooks/useTheme', () => ({ useTheme: () => ({ resolved: 'light' }) }))
jest.mock('@xyflow/react', () => ({
  ReactFlow: ({ children }: { children: ReactNode }) => <div role="application">{children}</div>,
  Background: () => null,
  Controls: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  ControlButton: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props}>{children}</button>,
  Handle: () => null, Position: { Left: 'left', Right: 'right' },
  useNodesInitialized: () => true,
  useOnViewportChange: () => undefined,
  useReactFlow: () => ({
    viewportInitialized: true,
    getNodes: mockGetNodes, setNodes: mockSetNodes, setEdges: mockSetEdges, getNodesBounds: mockGetBounds,
    getViewport: mockGetViewport, setViewport: mockSetViewport,
  }),
}))

describe('TreeCanvas', () => {
  beforeEach(() => { jest.clearAllMocks(); mockNodes = [] })
  it('highlights a fork path without selecting, moving or mutating nodes', () => {
    let tree = applyTreeCommand(createTreeWorkspace({
      name: 'Path preview', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
    }), { type: 'add', parentId: null, prompt: 'Root' })
    tree = applyTreeCommand(tree, { type: 'add', parentId: tree.nodes[0].id, prompt: 'Child' })
    const props = { workspace: tree, selectedId: tree.nodes[1].id, showPruned: false, disabled: false, onSelect: jest.fn(), onMove: jest.fn(), onGroup: jest.fn() }
    const view = render(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} /></FluentProvider>)
    const positions = mockNodes.map((node) => node.position)
    const viewportCalls = mockSetViewport.mock.calls.length
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} highlightedNodeIds={tree.nodes.map((node) => node.id)} /></FluentProvider>)
    expect(mockNodes.every((node) => node.data.pathPreview === true)).toBe(true)
    expect(mockNodes.map((node) => node.position)).toEqual(positions)
    expect(mockSetViewport).toHaveBeenCalledTimes(viewportCalls)
    expect(props.onGroup).not.toHaveBeenCalled()
    expect(props.onSelect).not.toHaveBeenCalled()
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} /></FluentProvider>)
    expect(mockNodes.every((node) => node.data.pathPreview === false)).toBe(true)
  })
  it('keeps existing positions and viewport during response/content updates', async () => {
    const tree = applyTreeCommand(createTreeWorkspace({
      name: 'Stable', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
    }), { type: 'add', parentId: null, prompt: 'Short prompt' })
    const props = { workspace: tree, selectedId: tree.nodes[0].id, showPruned: false, disabled: false, onSelect: jest.fn(), onMove: jest.fn(), onGroup: jest.fn() }
    const view = render(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} /></FluentProvider>)
    await waitFor(() => expect(mockNodes).toHaveLength(1))
    const position = { ...mockNodes[0].position }
    const viewportCalls = mockSetViewport.mock.calls.length
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props}
      workspace={{ ...tree, nodes: [{ ...tree.nodes[0], error: 'A long response that changes only card content' }] }} /></FluentProvider>)
    expect(mockNodes[0].position).toEqual(position)
    expect(mockSetViewport).toHaveBeenCalledTimes(viewportCalls)
    expect(screen.getByRole('application')).toBeInTheDocument()
  })
  it('preserves a live stack drag when execution changes after an earlier saved move', () => {
    let tree = applyTreeCommand(createTreeWorkspace({
      name: 'Drag', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
    }), { type: 'add', parentId: null, prompt: 'Original' })
    tree = applyTreeCommand(tree, { type: 'move', nodeId: tree.nodes[0].id, position: { x: 30, y: 40 } })
    tree = applyTreeCommand(tree, { type: 'sample', nodeId: tree.nodes[0].id, count: 2 })
    const props = { workspace: tree, selectedId: tree.nodes[0].id, showPruned: false, disabled: false, onSelect: jest.fn(), onMove: jest.fn(), onGroup: jest.fn() }
    const view = render(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} /></FluentProvider>)
    mockNodes = [{ ...mockNodes[0], dragging: true, position: { x: 100, y: 200 } }]
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} workspace={{
      ...tree, nodes: tree.nodes.map((node, index) => index === 0 ? { ...node, status: 'running' } : node),
    }} /></FluentProvider>)
    expect(mockNodes[0].position).toEqual({ x: 100, y: 200 })
    expect(props.onMove).not.toHaveBeenCalled()
  })
  it('isolates a focused sample group and restores the overview with unchanged positions', async () => {
    let tree = applyTreeCommand(createTreeWorkspace({
      name: 'Groups', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
    }), { type: 'add', parentId: null, prompt: 'Original' })
    tree = applyTreeCommand(tree, { type: 'sample', nodeId: tree.nodes[0].id, count: 2 })
    tree = applyTreeCommand(tree, { type: 'add', parentId: null, prompt: 'Unrelated' })
    const props = { workspace: tree, selectedId: tree.nodes[0].id, showPruned: false, disabled: false, onSelect: jest.fn(), onMove: jest.fn(), onGroup: jest.fn() }
    const view = render(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} /></FluentProvider>)
    const overview = mockNodes.map((node) => ({ id: node.id, position: node.position }))
    const group = tree.groups?.[0]
    expect(group).toBeDefined()
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} focusedGroupId={group?.id} /></FluentProvider>)
    expect(mockNodes.map((node) => node.id).sort()).toEqual(group?.nodeIds.slice().sort())
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} focusedGroupId={group?.id}
      workspace={{ ...tree, nodes: tree.nodes.map((node) => group?.nodeIds.includes(node.id) ? { ...node, pruned: true } : node) }} /></FluentProvider>)
    expect(mockNodes).toHaveLength(0)
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} /></FluentProvider>)
    expect(mockNodes.map((node) => ({ id: node.id, position: node.position }))).toEqual(overview)
    expect(mockSetViewport).toHaveBeenLastCalledWith({ x: 20, y: 30, zoom: 0.8 })
  })
  it.each([0, 1])('does not resurrect cleared overview overrides after focus (generation %i)', (generation: number) => {
    let tree = applyTreeCommand(createTreeWorkspace({
      name: 'Generation', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
    }), { type: 'add', parentId: null, prompt: 'Original' })
    tree = applyTreeCommand(tree, { type: 'sample', nodeId: tree.nodes[0].id, count: 2 })
    tree = applyTreeCommand(tree, { type: 'move', nodeId: tree.nodes[0].id, position: { x: 777, y: 888 } })
    const props = { workspace: tree, selectedId: tree.nodes[0].id, showPruned: false, disabled: false, onSelect: jest.fn(), onMove: jest.fn(), onGroup: jest.fn() }
    const view = render(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} layoutVersion={0} /></FluentProvider>)
    expect(mockNodes[0].position).toEqual({ x: 777, y: 888 })
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} workspace={applyTreeCommand(tree, { type: 'autoLayout' })} layoutVersion={0} /></FluentProvider>)
    expect(mockNodes[0].position).not.toEqual({ x: 777, y: 888 })
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} layoutVersion={0} /></FluentProvider>)
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} focusedGroupId={tree.groups?.[0].id} layoutVersion={0} /></FluentProvider>)
    tree = applyTreeCommand(tree, { type: 'autoLayout' })
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} workspace={tree} focusedGroupId={tree.groups?.[0].id} layoutVersion={generation} /></FluentProvider>)
    view.rerender(<FluentProvider theme={webLightTheme}><TreeCanvas {...props} workspace={tree} layoutVersion={generation} /></FluentProvider>)
    expect(mockNodes[0].position).not.toEqual({ x: 777, y: 888 })
    expect(tree.nodes.every((node) => node.position === undefined)).toBe(true)
  })
  it('uses node sizes, curved edges and explicit inherited pruning', () => {
    let tree = applyTreeCommand(createTreeWorkspace({
      name: 'Presentation', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
    }), { type: 'add', parentId: null, prompt: 'Original' })
    tree = applyTreeCommand(tree, { type: 'add', parentId: tree.nodes[0].id, prompt: 'Child' })
    tree = applyTreeCommand(tree, { type: 'resize', nodeId: tree.nodes[0].id, size: { width: 480, height: 440 } })
    tree = applyTreeCommand(tree, { type: 'prune', nodeId: tree.nodes[0].id, pruned: true })
    render(<FluentProvider theme={webLightTheme}><TreeCanvas workspace={tree} selectedId={null} showPruned disabled={false}
      onSelect={jest.fn()} onMove={jest.fn()} onGroup={jest.fn()} /></FluentProvider>)
    expect(mockNodes[0].style).toEqual({ width: 480, height: 440 })
    expect(mockNodes.every((node) => node.data.pruned)).toBe(true)
    expect(mockSetEdges).toHaveBeenLastCalledWith([expect.objectContaining({ type: 'default' })])
  })
})
