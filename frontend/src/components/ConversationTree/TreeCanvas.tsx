import { memo, useCallback, useEffect, useMemo, useRef } from 'react'

import { Badge, Button, mergeClasses, Text } from '@fluentui/react-components'
import { ArrowFitRegular, CheckmarkCircleRegular, ClockRegular, DismissCircleRegular, PinRegular, TargetRegular, WarningRegular } from '@fluentui/react-icons'
import {
  Background, ControlButton, Controls, Handle, NodeResizer, Position, ReactFlow, useNodesInitialized, useOnViewportChange, useReactFlow,
  type Edge, type Node, type NodeChange, type NodeProps, type Viewport,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { useTheme } from '@/hooks/useTheme'
import type { TreeCommand, TreeGroup, TreeNode, TreeWorkspace } from '@/types'

import { useConversationTreeStyles } from './ConversationTree.styles'
import { getTreeSettings, isNodeHidden } from './treeModel'
import { layoutTree } from './treeLayout'
import TreeScoreMeter from './TreeScoreMeter'
import { NODE_SIZES } from './treePresentation'

type TurnFlowNode = Node<{
  turn: TreeNode
  settings: ReturnType<typeof getTreeSettings>
  group?: TreeGroup
  queued: boolean
  renderKey: string
  onGroup: (command: TreeCommand) => void
  pruned: boolean
  size: { width: number; height: number }
  onFocusGroup: (groupId: string) => void
  onResizeState: (resizing: boolean) => void
  focused: boolean
}, 'turn'>

function responsePreview(turn: TreeNode): string {
  return (turn.error || turn.messages?.filter((message) => message.role !== 'user')
    .flatMap((message) => message.message_pieces)
    .map((piece) => piece.converted_value_data_type === 'text' ? piece.converted_value.slice(0, 4000) : `[${piece.converted_value_data_type}]`)
    .join('\n') || 'Not run yet').slice(0, 4000)
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
  focusedGroupId?: string | null
  onFocusGroup?: (groupId: string | null) => void
}

const TurnCard = memo(function TurnCard({ data, selected }: NodeProps<TurnFlowNode>) {
  const styles = useConversationTreeStyles()
  const { turn, group } = data
  const state = data.queued && turn.status === 'draft' ? 'queued' : turn.status
  const activeIndex = group?.nodeIds.indexOf(turn.id) ?? 0
  return (
    <>
    <NodeResizer isVisible={selected} minWidth={220} minHeight={180} maxWidth={900} maxHeight={1000}
      onResizeStart={() => { data.onResizeState(true) }}
      onResizeEnd={(_, size) => {
        data.onGroup({ type: 'resize', nodeId: turn.id, size: { width: size.width, height: size.height },
          position: data.focused ? undefined : { x: size.x, y: size.y } })
        data.onResizeState(false)
      }} />
    <div className={mergeClasses(styles.card, group?.collapsed ? styles.stackedCard : undefined)}
      data-selected={selected} data-state={state} data-pruned={data.pruned} data-kept={turn.kept}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className={styles.row}>
        <Badge appearance={state === 'draft' ? 'outline' : 'filled'}
          color={state === 'error' ? 'danger' : state === 'completed' ? 'brand' : state === 'draft' ? 'subtle' : 'informative'}
          icon={state === 'completed' ? <CheckmarkCircleRegular /> : state === 'error' ? <WarningRegular /> : state === 'running' || state === 'queued' ? <ClockRegular /> : undefined}>
          {state}
        </Badge>
        {data.pruned ? <Badge appearance="outline" icon={<DismissCircleRegular />}>Pruned</Badge>
          : turn.kept && <Badge appearance="outline" icon={<PinRegular />}>Kept</Badge>}
        {(turn.attempts?.length ?? 0) > 0 && <Text size={100}>Attempt {(turn.attempts?.length ?? 0) + 1}</Text>}
      </div>
      <div className={data.size.height >= 360 ? styles.expandedPrompt : styles.preview}>{turn.prompt.slice(0, data.size.height >= 360 ? 2000 : 400)}</div>
      <div className={styles.pipelinePreview}>{turn.converters.map((converter) => converter.type).join(' > ')}</div>
      <div className={mergeClasses(data.size.height >= 360 ? styles.expandedResponse : styles.preview, styles.responsePreview, 'nodrag', 'nowheel')}>
        {responsePreview(turn)}
      </div>
      <TreeScoreMeter node={turn} settings={data.settings} />
      {group?.collapsed && <div className={mergeClasses(styles.row, 'nodrag', 'nopan')}>
        <Button size="small" className={styles.button} aria-label="Previous stack member" disabled={activeIndex <= 0}
          onClick={() => { data.onGroup({ type: 'group', groupId: group.id, activeNodeId: group.nodeIds[activeIndex - 1] }) }}>&lt;</Button>
        <Text size={200}>{group.kind === 'sample' ? 'Samples' : 'Variants'} {activeIndex + 1}/{group.nodeIds.length}</Text>
        <Button size="small" className={styles.button} aria-label="Next stack member" disabled={activeIndex >= group.nodeIds.length - 1}
          onClick={() => { data.onGroup({ type: 'group', groupId: group.id, activeNodeId: group.nodeIds[activeIndex + 1] }) }}>&gt;</Button>
        <Button size="small" className={styles.button} onClick={() => { data.onFocusGroup(group.id) }}>Focus group</Button>
      </div>}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div></>
  )
}, (previous, next) => previous.selected === next.selected && previous.data.renderKey === next.data.renderKey)

const NODE_TYPES = { turn: TurnCard }
const EMPTY_NODE_IDS: string[] = []
const MIN_ZOOM = 0.01
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

function visibleNodes(workspace: TreeWorkspace, showPruned: boolean, groups: TreeGroup[], focusedGroupId?: string | null): TreeNode[] {
  const hidden = new Set<string>()
  for (const group of groups) {
    if (group.collapsed) for (const id of group.nodeIds) if (id !== group.activeNodeId) hidden.add(id)
  }
  const byId = new Map(workspace.nodes.map((node) => [node.id, node]))
  const focused = groups.find((group) => group.id === focusedGroupId)
  const included = focused ? new Set(focused.nodeIds) : null
  if (included) {
    for (let changed = true; changed;) {
      changed = false
      for (const node of workspace.nodes) if (node.parentId && included.has(node.parentId) && !included.has(node.id)) {
        included.add(node.id); changed = true
      }
    }
    for (const id of focused?.nodeIds ?? []) {
      let node = byId.get(id)
      while (node?.parentId) { included.add(node.parentId); node = byId.get(node.parentId) }
    }
  }
  return workspace.nodes.filter((node) => {
    if (included && !included.has(node.id)) return false
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
  onFocusStable: (groupId: string) => void
  onResizeStable: (resizing: boolean) => void
}

function GraphSync({ workspace, selectedId, showPruned, layoutVersion = 0, queuedIds = EMPTY_NODE_IDS, onGroupStable, onFocusStable, onResizeStable, focusedGroupId }: GraphSyncProps) {
  const { setNodes, setEdges, getNodes, getNodesBounds, getViewport, setViewport, viewportInitialized } = useReactFlow<TurnFlowNode>()
  const initialized = useNodesInitialized()
  const didFit = useRef(false)
  const priorLayout = useRef(layoutVersion)
  const positions = useRef(new Map<string, { x: number; y: number }>())
  const persisted = useRef(new Map<string, string>())
  const groupPositions = useRef(new Map<string, { x: number; y: number }>())
  const selection = useRef<string | null>(null)
  const lastViewport = useRef<Viewport | null>(null)
  const rememberViewport = useCallback((viewport: Viewport) => { lastViewport.current = viewport }, [])
  useOnViewportChange({ onChange: rememberViewport })
  const groups = useMemo(() => visibleGroups(workspace, showPruned).map((group) =>
    group.id === focusedGroupId ? { ...group, collapsed: false } : group), [workspace, showPruned, focusedGroupId])
  const nodes = useMemo(() => visibleNodes(workspace, showPruned, groups, focusedGroupId), [workspace, showPruned, groups, focusedGroupId])
  const memoryKey = `${workspace.id}:${focusedGroupId ?? 'overview'}`
  useEffect(() => {
    positions.current = new Map(CANVAS_MEMORY.get(memoryKey)?.positions)
    return () => {
      if (!didFit.current) return
      const latest = new Map(positions.current)
      for (const node of getNodes()) latest.set(node.id, node.position)
      // The Flow store can reset before child cleanup; retain the last live viewport.
      CANVAS_MEMORY.set(memoryKey, { viewport: lastViewport.current ?? getViewport(), positions: latest })
    }
  }, [memoryKey, getNodes, getViewport])

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
    const settings = getTreeSettings(workspace)
    const defaultSize = NODE_SIZES[settings.nodeSize ?? 'standard']
    const initialLayout = layoutTree(
      nodes,
      Object.fromEntries(nodes.map((node) => [node.id, node.size?.height ?? defaultSize.height])),
      Object.fromEntries(nodes.map((node) => [node.id, node.size?.width ?? defaultSize.width])),
    )
    if (reset) { positions.current.clear(); groupPositions.current.clear() }
    const nextNodes: TurnFlowNode[] = nodes.map((turn) => {
      const group = groups.find((entry) => entry.collapsed && entry.activeNodeId === turn.id)
      const old = existing.get(turn.id)
      const savedPosition = focusedGroupId ? undefined : turn.position
      const storedPosition = JSON.stringify(savedPosition)
      const changedPosition = savedPosition && persisted.current.get(turn.id) !== storedPosition
      const clearedPosition = persisted.current.has(turn.id) && persisted.current.get(turn.id) !== storedPosition && !savedPosition
      let position = positions.current.get(turn.id) ?? old?.position
      if (changedPosition) position = savedPosition
      if (clearedPosition) position = initialLayout[turn.id]
      persisted.current.set(turn.id, storedPosition)
      if (!position || reset) {
        position = initialLayout[turn.id] ?? { x: 0, y: 0 }
        if (!reset && didFit.current) {
          const parentPosition = turn.parentId ? positions.current.get(turn.parentId) : undefined
          const parent = workspace.nodes.find((node) => node.id === turn.parentId)
          position = { x: parentPosition ? parentPosition.x + (parent?.size?.width ?? defaultSize.width) + 70 : 0, y: parentPosition?.y ?? 0 }
          const occupied = (candidate: { x: number; y: number }): boolean => [...positions.current.entries()]
            .some(([id, other]) => {
              const otherSize = workspace.nodes.find((node) => node.id === id)?.size ?? defaultSize
              const size = turn.size ?? defaultSize
              return candidate.x < other.x + otherSize.width + 30 && candidate.x + size.width + 30 > other.x
                && candidate.y < other.y + otherSize.height + 30 && candidate.y + size.height + 30 > other.y
            })
          while (occupied(position)) {
            position = { ...position, y: position.y + (turn.size?.height ?? defaultSize.height) + 48 }
          }
        }
      } else if (old && !reset && !clearedPosition && !changedPosition) {
        // React Flow owns live dragging; content updates must retain its current position.
        position = old.position
      }
      if (group) {
        if (changedPosition || old?.dragging || old?.resizing) groupPositions.current.set(group.id, position)
        position = groupPositions.current.get(group.id) ?? position
        groupPositions.current.set(group.id, position)
      }
      positions.current.set(turn.id, position)
      const queued = queuedIds.includes(turn.id)
      const pruned = isNodeHidden(workspace, turn.id)
      const size = turn.size ?? defaultSize
      const renderKey = JSON.stringify([turn.prompt.slice(0, 2000), turn.converters.map((converter) => converter.type), turn.status, responsePreview(turn),
        turn.scoreRuns?.map((result) => [result.id, result.status, result.scores.map((score) => [score.score_type, score.score_value, score.status])]),
        turn.kept, pruned, size, turn.attempts?.length, group, queued, settings.scorers, settings.primaryScorerId])
      if (old && old.data.renderKey === renderKey && old.selected === (turn.id === selectedId) &&
        old.position.x === position.x && old.position.y === position.y) return old
      return {
        ...old, id: turn.id, type: 'turn', position, selected: turn.id === selectedId,
        width: old?.resizing ? old.width : size.width,
        height: old?.resizing ? old.height : size.height,
        style: { width: old?.resizing ? old.width : size.width, height: old?.resizing ? old.height : size.height },
        data: { turn, settings, group, queued, pruned, size, renderKey, onGroup: onGroupStable, onFocusGroup: onFocusStable,
          onResizeState: onResizeStable, focused: !!focusedGroupId },
        ariaLabel: `Prompt: ${turn.prompt.slice(0, 80)} (${queued ? 'queued' : turn.status})`,
      }
    })
    const edges: Edge[] = nodes.filter((node) => node.parentId !== null)
      .map((node) => ({
        id: `history-${node.id}`, source: node.parentId ?? '', target: node.id,
        type: (settings.edgeStyle ?? 'bezier') === 'bezier' ? 'default' : settings.edgeStyle,
        style: isNodeHidden(workspace, node.id) ? { opacity: 0.35, strokeDasharray: '4 4' } : undefined,
      }))
    setNodes(nextNodes)
    setEdges(edges)
    if (reset) requestAnimationFrame(fit)
  }, [nodes, groups, workspace, selectedId, layoutVersion, queuedIds, getNodes, setNodes, setEdges, fit, onGroupStable, onFocusStable, onResizeStable, focusedGroupId])

  useEffect(() => {
    if (!initialized || !viewportInitialized) return
    if (!didFit.current) {
      didFit.current = true
      selection.current = selectedId
      const remembered = CANVAS_MEMORY.get(memoryKey)
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
  }, [initialized, viewportInitialized, selectedId, nodes, fit, getNodes, getNodesBounds, getViewport, setViewport, memoryKey])

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
  const onFocus = useCallback((groupId: string) => { callbacks.current.onFocusGroup?.(groupId) }, [])
  const resizing = useRef(false)
  const onResize = useCallback((active: boolean) => {
    if (active) resizing.current = true
    else queueMicrotask(() => { resizing.current = false })
  }, [])
  const onClick = useCallback((_: React.MouseEvent, node: TurnFlowNode) => { callbacks.current.onSelect(node.id) }, [])
  const onChange = useCallback((changes: NodeChange<TurnFlowNode>[]) => {
    for (const change of changes) {
      if (change.type === 'position' && change.position && change.dragging === false) {
        if (!resizing.current && !callbacks.current.focusedGroupId) callbacks.current.onMove(change.id, change.position)
      }
      if (change.type === 'select' && change.selected) callbacks.current.onSelect(change.id)
    }
  }, [])
  return (
    <section className={styles.canvas} aria-label="Conversation graph" data-testid="conversation-graph">
      <ReactFlow<TurnFlowNode> key={`${props.workspace.id}:${props.focusedGroupId ?? 'overview'}`}
        defaultNodes={EMPTY_NODES} defaultEdges={EMPTY_EDGES} nodeTypes={NODE_TYPES} colorMode={resolved === 'light' ? 'light' : 'dark'}
        minZoom={MIN_ZOOM} maxZoom={1.5} nodesConnectable={false} edgesReconnectable={false}
        multiSelectionKeyCode={null} selectionKeyCode={null} deleteKeyCode={null} autoPanOnNodeFocus={false}
        onNodeClick={onClick} onNodesChange={onChange}>
        <Background />
        <GraphSync {...props} onGroupStable={onGroup} onFocusStable={onFocus} onResizeStable={onResize} />
      </ReactFlow>
    </section>
  )
}
