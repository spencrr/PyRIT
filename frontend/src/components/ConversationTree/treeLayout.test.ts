import type { TreeNode } from '@/types'

import { layoutTree } from './treeLayout'

function node(id: string, parentId: string | null = null): TreeNode {
  return { id, parentId, prompt: id, status: 'draft', converters: [], kept: false, pruned: false }
}

describe('layoutTree', () => {
  it('places a forest in columns without overlapping unequal-height sibling subtrees', () => {
    const nodes = [node('a'), node('b', 'a'), node('c', 'a'), node('d', 'b'), node('e')]
    const heights = { a: 180, b: 320, c: 160, d: 150, e: 200 }
    const positions = layoutTree(nodes, heights)
    expect(positions.a.x).toBe(positions.e.x)
    expect(positions.b.x).toBeGreaterThan(positions.a.x)
    expect(positions.d.x).toBeGreaterThan(positions.b.x)
    expect(positions.b.y + heights.b).toBeLessThan(positions.c.y)
    expect(positions.e.y).toBeGreaterThan(positions.c.y + heights.c)
    expect(Object.values(positions).every((position) => Number.isFinite(position.x) && Number.isFinite(position.y))).toBe(true)
  })

  it('is stable across selection-independent metadata changes and descendant-before-parent storage', () => {
    const nodes = [node('b', 'a'), node('a'), node('c', 'a')]
    const positions = layoutTree(nodes)
    const changed = nodes.map((item: TreeNode) => ({ ...item, kept: true, prompt: 'A different preview' }))
    expect(layoutTree(changed)).toEqual(positions)
    expect(positions.b.x).toBe(positions.c.x)
  })

  it('uses the widest node at each depth so wide cards do not collide with deeper columns', () => {
    const nodes = [node('root'), node('wide', 'root'), node('peer', 'root'), node('leaf', 'wide')]
    const heights = { root: 180, wide: 220, peer: 180, leaf: 180 }
    const widths = { root: 300, wide: 900, peer: 250, leaf: 250 }
    const positions = layoutTree(nodes, heights, widths)
    expect(positions.wide.x).toBe(350)
    expect(positions.peer.x).toBe(positions.wide.x)
    expect(positions.leaf.x).toBe(positions.wide.x + widths.wide + 50)
  })

  it('lays out 300 nodes deterministically and handles empty forests', () => {
    const nodes = Array.from({ length: 300 }, (_, index: number) => node(`node-${index}`, index ? `node-${index - 1}` : null))
    expect(Object.keys(layoutTree(nodes))).toHaveLength(300)
    expect(layoutTree([])).toEqual({})
  })
})
