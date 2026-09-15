import type { TreeNode } from '@/types'

const COLUMN_GAP = 350
const ROW_GAP = 48
const DEFAULT_HEIGHT = 160

/** A stable layered forest. Selection is deliberately absent from the layout inputs. */
export function layoutTree(
  nodes: TreeNode[],
  heights: Readonly<Record<string, number>> = {},
): Record<string, { x: number; y: number }> {
  const children = new Map<string | null, TreeNode[]>()
  for (const node of nodes) children.set(node.parentId, [...(children.get(node.parentId) ?? []), node])
  const spans = new Map<string, number>()
  function measure(node: TreeNode): number {
    const descendants = children.get(node.id) ?? []
    const span = Math.max(heights[node.id] ?? DEFAULT_HEIGHT,
      descendants.reduce((total: number, child: TreeNode) => total + measure(child), 0)
      + Math.max(0, descendants.length - 1) * ROW_GAP)
    spans.set(node.id, span)
    return span
  }
  const positions: Record<string, { x: number; y: number }> = {}
  function place(node: TreeNode, depth: number, top: number): void {
    const span = spans.get(node.id) ?? DEFAULT_HEIGHT
    positions[node.id] = { x: depth * COLUMN_GAP, y: top + (span - (heights[node.id] ?? DEFAULT_HEIGHT)) / 2 }
    const descendants = children.get(node.id) ?? []
    const childSpan = descendants.reduce((total: number, child: TreeNode) => total + (spans.get(child.id) ?? DEFAULT_HEIGHT), 0)
      + Math.max(0, descendants.length - 1) * ROW_GAP
    let cursor = top + (span - childSpan) / 2
    for (const child of descendants) {
      place(child, depth + 1, cursor)
      cursor += (spans.get(child.id) ?? DEFAULT_HEIGHT) + ROW_GAP
    }
  }
  let top = 0
  for (const root of children.get(null) ?? []) {
    measure(root)
    place(root, 0, top)
    top += (spans.get(root.id) ?? DEFAULT_HEIGHT) + ROW_GAP
  }
  return positions
}
