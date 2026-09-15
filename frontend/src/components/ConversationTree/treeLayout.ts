import type { TreeNode } from '@/types'

const COLUMN_GAP = 50
const ROW_GAP = 48
const DEFAULT_HEIGHT = 160
const DEFAULT_WIDTH = 300

function finitePositive(value: number | undefined, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : fallback
}

/** A stable layered forest. Selection is deliberately absent from the layout inputs. */
export function layoutTree(
  nodes: TreeNode[],
  heights: Readonly<Record<string, number>> = {},
  widths: Readonly<Record<string, number>> = {},
): Record<string, { x: number; y: number }> {
  const children = new Map<string | null, TreeNode[]>()
  for (const node of nodes) children.set(node.parentId, [...(children.get(node.parentId) ?? []), node])
  const depthWidths = new Map<number, number>()
  const spans = new Map<string, number>()
  function measure(node: TreeNode, depth: number): number {
    depthWidths.set(depth, Math.max(depthWidths.get(depth) ?? 0, finitePositive(widths[node.id], DEFAULT_WIDTH)))
    const descendants = children.get(node.id) ?? []
    const span = Math.max(finitePositive(heights[node.id], DEFAULT_HEIGHT),
      descendants.reduce((total: number, child: TreeNode) => total + measure(child, depth + 1), 0)
      + Math.max(0, descendants.length - 1) * ROW_GAP)
    spans.set(node.id, span)
    return span
  }
  const depthOffsets = new Map<number, number>()
  function xForDepth(depth: number): number {
    const cached = depthOffsets.get(depth)
    if (cached !== undefined) return cached
    let offset = 0
    for (let current = 0; current < depth; current += 1) offset += (depthWidths.get(current) ?? DEFAULT_WIDTH) + COLUMN_GAP
    depthOffsets.set(depth, offset)
    return offset
  }
  const positions: Record<string, { x: number; y: number }> = {}
  function place(node: TreeNode, depth: number, top: number): void {
    const height = finitePositive(heights[node.id], DEFAULT_HEIGHT)
    const span = spans.get(node.id) ?? height
    positions[node.id] = { x: xForDepth(depth), y: top + (span - height) / 2 }
    const descendants = children.get(node.id) ?? []
    const childSpan = descendants.reduce((total: number, child: TreeNode) =>
      total + (spans.get(child.id) ?? finitePositive(heights[child.id], DEFAULT_HEIGHT)), 0)
      + Math.max(0, descendants.length - 1) * ROW_GAP
    let cursor = top + (span - childSpan) / 2
    for (const child of descendants) {
      place(child, depth + 1, cursor)
      cursor += (spans.get(child.id) ?? finitePositive(heights[child.id], DEFAULT_HEIGHT)) + ROW_GAP
    }
  }
  let top = 0
  const roots = children.get(null) ?? []
  for (const root of roots) measure(root, 0)
  for (const root of roots) {
    place(root, 0, top)
    top += (spans.get(root.id) ?? finitePositive(heights[root.id], DEFAULT_HEIGHT)) + ROW_GAP
  }
  return positions
}
