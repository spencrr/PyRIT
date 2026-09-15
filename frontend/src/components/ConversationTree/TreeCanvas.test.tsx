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
  useReactFlow: () => ({
    getNodes: mockGetNodes, setNodes: mockSetNodes, setEdges: mockSetEdges, getNodesBounds: mockGetBounds,
    getViewport: mockGetViewport, setViewport: mockSetViewport,
  }),
}))

describe('TreeCanvas', () => {
  beforeEach(() => { jest.clearAllMocks(); mockNodes = [] })
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
})
