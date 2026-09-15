import { memo, useCallback, useEffect, useMemo, useRef } from 'react'

import { Badge, Button, mergeClasses, Text } from '@fluentui/react-components'
import { ArrowFitRegular, CheckmarkCircleRegular, ClockRegular, TargetRegular, WarningRegular } from '@fluentui/react-icons'
import {
  Background, ControlButton, Controls, Handle, Position, ReactFlow, useNodesInitialized, useReactFlow,
  type Edge, type Node, type NodeChange, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { useTheme } from '@/hooks/useTheme'
import type { TreeCommand, TreeGroup, TreeNode, TreeWorkspace } from '@/types'

import { useConversationTreeStyles } from './ConversationTree.styles'
import { getTreeSettings, isNodeHidden } from './treeModel'
import { layoutTree } from './treeLayout'
import TreeScoreMeter from './TreeScoreMeter'

type TurnFlowNode = Node<{
  turn: TreeNode
  settings: ReturnType<typeof getTreeSettings>
  group?: TreeGroup
  queued: boolean
  renderKey: string
  onGroup: (command: TreeCommand) => void
}, 'turn'>

function responsePreview(turn: TreeNode): string {
  return (turn.error || turn.messages?.filter((message) => message.role !== 'user')
    .flatMap((message) => message.message_pieces)
    .map((piece) => piece.converted_value_data_type === 'text' ? piece.converted_value.slice(0, 600) : `[${piece.converted_value_data_type}]`)
    .join('\n') || 'Not run yet').slice(0, 600)
}

interface TreeCanvasProps {
  workspace: TreeWorkspace
  selectedId: string | null
  showPruned: boolean
  disabled: boolean
  layoutVersion?: number
  queuedIds?: string[]
  onSelect: (id: string) => void
  onMove: (id: string, position: { x: number; y: number }) => void
  onGroup: (command: TreeCommand) => void
}

const TurnCard = memo(function TurnCard({ data, selected }: NodeProps<TurnFlowNode>) {
  const styles = useConversationTreeStyles()
  const { turn, group } = data
  const state = data.queued && turn.status === 'draft' ? 'queued' : turn.status
  const activeIndex = group?.nodeIds.indexOf(turn.id) ?? 0
  return (
    <div className={mergeClasses(styles.card, group?.collapsed ? styles.stackedCard : undefined)}
      data-selected={selected} data-state={state}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className={styles.row}>
        <Badge appearance={state === 'draft' ? 'outline' : 'filled'}
          color={state === 'error' ? 'danger' : state === 'completed' ? 'brand' : state === 'draft' ? 'subtle' : 'informative'}
          icon={state === 'completed' ? <CheckmarkCircleRegular /> : state === 'error' ? <WarningRegular /> : state === 'running' || state === 'queued' ? <ClockRegular /> : undefined}>
          {state}
        </Badge>
        {turn.kept && <Badge appearance="outline">Kept</Badge>}
        {(turn.attempts?.length ?? 0) > 0 && <Text size={100}>Attempt {(turn.attempts?.length ?? 0) + 1}</Text>}
      </div>
      <div className={styles.preview}>{turn.prompt.slice(0, 400)}</div>
      <div className={styles.pipelinePreview}>{turn.converters.map((converter) => converter.type).join(' > ')}</div>
      <div className={mergeClasses(styles.preview, styles.responsePreview)}>{responsePreview(turn)}</div>
      <TreeScoreMeter node={turn} settings={data.settings} />
      {group?.collapsed && <div className={mergeClasses(styles.row, 'nodrag', 'nopan')}>
        <Button size="small" className={styles.button} aria-label="Previous stack member" disabled={activeIndex <= 0}
          onClick={() => { data.onGroup({ type: 'group', groupId: group.id, activeNodeId: group.nodeIds[activeIndex - 1] }) }}>&lt;</Button>
        <Text size={200}>{group.kind === 'sample' ? 'Samples' : 'Variants'} {activeIndex + 1}/{group.nodeIds.length}</Text>
        <Button size="small" className={styles.button} aria-label="Next stack member" disabled={activeIndex >= group.nodeIds.length - 1}
          onClick={() => { data.onGroup({ type: 'group', groupId: group.id, activeNodeId: group.nodeIds[activeIndex + 1] }) }}>&gt;</Button>
        <Button size="small" className={styles.button} onClick={() => { data.onGroup({ type: 'group', groupId: group.id, collapsed: false }) }}>Expand</Button>
      </div>}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  )
}, (previous, next) => previous.selected === next.selected && previous.data.renderKey === next.data.renderKey)

const NODE_TYPES = { turn: TurnCard }
const MIN_ZOOM = 0.01
const CARD_HEIGHT = 240
const CANVAS_MEMORY = new Map<string, {
  viewport: { x: number; y: number; zoom: number }
  positions: Map<string, { x: number; y: number }>
}>()

function visibleGroups(workspace: TreeWorkspace, showPruned: boolean): TreeGroup[] {
  return (workspace.groups ?? []).flatMap((group) => {
    const ids = group.nodeIds.filter((id) => showPruned || !isNodeHidden(workspace, id))
    return ids.length ? [{ ...group, nodeIds: ids, activeNodeId: ids.includes(group.activeNodeId) ? group.activeNodeId : ids[0] }] : []
  })
}

function visibleNodes(workspace: TreeWorkspace, showPruned: boolean, groups: TreeGroup[]): TreeNode[] {
  const hidden = new Set<string>()
  for (const group of groups) {
    if (group.collapsed) for (const id of group.nodeIds) if (id !== group.activeNodeId) hidden.add(id)
  }
  const byId = new Map(workspace.nodes.map((node) => [node.id, node]))
  return workspace.nodes.filter((node) => {
    if (!showPruned && isNodeHidden(workspace, node.id)) return false
    let ancestor: TreeNode | undefined = node
    while (ancestor) {
      if (hidden.has(ancestor.id)) return false
      ancestor = ancestor.parentId ? byId.get(ancestor.parentId) : undefined
    }
    return true
  })
}

interface GraphSyncProps extends TreeCanvasProps {
  onGroupStable: (command: TreeCommand) => void
}

function GraphSync({ workspace, selectedId, showPruned, layoutVersion = 0, queuedIds = [], onGroupStable }: GraphSyncProps) {
  const { setNodes, setEdges, getNodes, getNodesBounds, getViewport, setViewport } = useReactFlow<TurnFlowNode>()
  const initialized = useNodesInitialized()
  const didFit = useRef(false)
  const priorLayout = useRef(layoutVersion)
  const positions = useRef(new Map<string, { x: number; y: number }>())
  const persisted = useRef(new Map<string, string>())
  const groupPositions = useRef(new Map<string, { x: number; y: number }>())
  const selection = useRef<string | null>(null)
  const groups = useMemo(() => visibleGroups(workspace, showPruned), [workspace, showPruned])
  const nodes = useMemo(() => visibleNodes(workspace, showPruned, groups), [workspace, showPruned, groups])
  useEffect(() => {
    positions.current = new Map(CANVAS_MEMORY.get(workspace.id)?.positions)
    return () => {
      const latest = new Map(positions.current)
      for (const node of getNodes()) latest.set(node.id, node.position)
      CANVAS_MEMORY.set(workspace.id, { viewport: getViewport(), positions: latest })
    }
  }, [workspace.id, getNodes, getViewport])

  const fit = useCallback(() => {
    const bounds = getNodesBounds(getNodes())
    const viewport = document.querySelector('[data-testid="conversation-graph"]')?.getBoundingClientRect()
    if (!viewport?.width || !viewport.height) return
    const zoom = Math.max(MIN_ZOOM, Math.min(1, viewport.width / (bounds.width * 1.2), viewport.height / (bounds.height * 1.2)))
    void setViewport({ x: viewport.width / 2 - (bounds.x + bounds.width / 2) * zoom, y: viewport.height / 2 - (bounds.y + bounds.height / 2) * zoom, zoom })
  }, [getNodesBounds, getNodes, setViewport])

  useEffect(() => {
    const reset = priorLayout.current !== layoutVersion
    priorLayout.current = layoutVersion
    const existing = new Map(getNodes().map((node) => [node.id, node]))
    const initialLayout = layoutTree(nodes, Object.fromEntries(nodes.map((node) => [node.id, CARD_HEIGHT])))
    if (reset) { positions.current.clear(); groupPositions.current.clear() }
    const settings = getTreeSettings(workspace)
    const nextNodes: TurnFlowNode[] = nodes.map((turn) => {
      const group = groups.find((entry) => entry.collapsed && entry.activeNodeId === turn.id)
      const old = existing.get(turn.id)
      const storedPosition = JSON.stringify(turn.position)
      const changedPosition = turn.position && persisted.current.get(turn.id) !== storedPosition
      let position = positions.current.get(turn.id) ?? old?.position
      if (changedPosition) position = turn.position
      persisted.current.set(turn.id, storedPosition)
      if (!position || reset) {
        position = initialLayout[turn.id] ?? { x: 0, y: 0 }
        if (!reset && didFit.current) {
          const parentPosition = turn.parentId ? positions.current.get(turn.parentId) : undefined
          position = { x: parentPosition ? parentPosition.x + 350 : 0, y: parentPosition?.y ?? 0 }
          const occupied = (candidate: { x: number; y: number }): boolean => [...positions.current.values()]
            .some((other) => Math.abs(other.x - candidate.x) < 300 && Math.abs(other.y - candidate.y) < CARD_HEIGHT + 30)
          while (occupied(position)) {
            position = { ...position, y: position.y + CARD_HEIGHT + 48 }
          }
        }
      } else if (old && !reset && persisted.current.get(turn.id) === storedPosition) {
        // React Flow owns live dragging; content updates must retain its current position.
        position = turn.position ?? old.position
      }
      if (group) {
        if (changedPosition) groupPositions.current.set(group.id, position)
        position = groupPositions.current.get(group.id) ?? position
        groupPositions.current.set(group.id, position)
      }
      positions.current.set(turn.id, position)
      const queued = queuedIds.includes(turn.id)
      const renderKey = JSON.stringify([turn.prompt.slice(0, 400), turn.converters.map((converter) => converter.type), turn.status, responsePreview(turn),
        turn.scoreRuns?.map((result) => [result.id, result.status, result.scores.map((score) => [score.score_type, score.score_value, score.status])]),
        turn.kept, turn.attempts?.length, group, queued, settings.scorers, settings.primaryScorerId])
      if (old && old.data.renderKey === renderKey && old.selected === (turn.id === selectedId) &&
        old.position.x === position.x && old.position.y === position.y) return old
      return {
        ...old, id: turn.id, type: 'turn', position, selected: turn.id === selectedId,
        data: { turn, settings, group, queued, renderKey, onGroup: onGroupStable },
        ariaLabel: `Prompt: ${turn.prompt.slice(0, 80)} (${queued ? 'queued' : turn.status})`,
      }
    })
    setNodes(nextNodes)
    const edges: Edge[] = nodes.filter((node) => node.parentId !== null)
      .map((node) => ({ id: `history-${node.id}`, source: node.parentId ?? '', target: node.id, type: 'smoothstep' }))
    setEdges(edges)
    if (reset) requestAnimationFrame(fit)
  }, [nodes, groups, workspace, selectedId, layoutVersion, queuedIds, getNodes, setNodes, setEdges, fit, onGroupStable])

  useEffect(() => {
    if (!initialized) return
    if (!didFit.current) {
      didFit.current = true
      selection.current = selectedId
      const remembered = CANVAS_MEMORY.get(workspace.id)
      if (remembered) void setViewport(remembered.viewport)
      else fit()
      return
    }
    if (selection.current === selectedId) return
    const selected = getNodes().find((node) => node.id === selectedId)
    const viewportSize = document.querySelector('[data-testid="conversation-graph"]')?.getBoundingClientRect()
    if (!selected || !viewportSize?.width || !viewportSize.height) return
    selection.current = selectedId
    const bounds = getNodesBounds([selected])
    const view = getViewport()
    const left = bounds.x * view.zoom + view.x
    const top = bounds.y * view.zoom + view.y
    if (left >= 0 && top >= 0 && left + bounds.width * view.zoom <= viewportSize.width && top + bounds.height * view.zoom <= viewportSize.height) return
    void setViewport({
      x: viewportSize.width / 2 - (bounds.x + bounds.width / 2) * view.zoom,
      y: viewportSize.height / 2 - (bounds.y + bounds.height / 2) * view.zoom, zoom: view.zoom,
    })
  }, [initialized, selectedId, nodes, fit, getNodes, getNodesBounds, getViewport, setViewport, workspace.id])

  function focusSelected(): void {
    const node = getNodes().find((item) => item.id === selectedId)
    const viewport = document.querySelector('[data-testid="conversation-graph"]')?.getBoundingClientRect()
    if (!node || !viewport?.width || !viewport.height) return
    const bounds = getNodesBounds([node])
    const zoom = getViewport().zoom
    void setViewport({ x: viewport.width / 2 - (bounds.x + bounds.width / 2) * zoom, y: viewport.height / 2 - (bounds.y + bounds.height / 2) * zoom, zoom })
  }
  return <Controls showInteractive={false} showFitView={false}>
    <ControlButton aria-label="Fit all" title="Fit all" onClick={fit}><ArrowFitRegular /></ControlButton>
    <ControlButton aria-label="Focus selected" title="Focus selected" onClick={focusSelected}><TargetRegular /></ControlButton>
  </Controls>
}

const EMPTY_NODES: TurnFlowNode[] = []
const EMPTY_EDGES: Edge[] = []

export default function TreeCanvas(props: TreeCanvasProps) {
  const styles = useConversationTreeStyles()
  const { resolved } = useTheme()
  const callbacks = useRef(props)
  useEffect(() => { callbacks.current = props }, [props])
  const onGroup = useCallback((command: TreeCommand) => { callbacks.current.onGroup(command) }, [])
  const onClick = useCallback((_: React.MouseEvent, node: TurnFlowNode) => { callbacks.current.onSelect(node.id) }, [])
  const onChange = useCallback((changes: NodeChange<TurnFlowNode>[]) => {
    for (const change of changes) {
      if (change.type === 'position' && change.position && change.dragging === false) {
        callbacks.current.onMove(change.id, change.position)
      }
      if (change.type === 'select' && change.selected) callbacks.current.onSelect(change.id)
    }
  }, [])
  return (
    <section className={styles.canvas} aria-label="Conversation graph" data-testid="conversation-graph">
      <ReactFlow<TurnFlowNode> defaultNodes={EMPTY_NODES} defaultEdges={EMPTY_EDGES} nodeTypes={NODE_TYPES} colorMode={resolved === 'light' ? 'light' : 'dark'}
        minZoom={MIN_ZOOM} maxZoom={1.5} nodesConnectable={false} edgesReconnectable={false}
        multiSelectionKeyCode={null} selectionKeyCode={null} deleteKeyCode={null} autoPanOnNodeFocus={false}
        onNodeClick={onClick} onNodesChange={onChange}>
        <Background />
        <GraphSync {...props} onGroupStable={onGroup} />
      </ReactFlow>
    </section>
  )
}
