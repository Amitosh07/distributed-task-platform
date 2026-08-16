import React, { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Badge } from '../ui/Badge'
import { formatDate } from '../ui'
import type { Workflow, WorkflowRun, WorkflowRunNode } from '../../types'

export interface DagViewerProps {
  workflow: Workflow
  run?: WorkflowRun
  className?: string
}

interface NodeLayout {
  key: string
  x: number
  y: number
  level: number
  taskType: string
  status: string
  taskId: string | null
  startedAt: string | null
  finishedAt: string | null
  errorMessage: string | null
}

const CARD_WIDTH = 220
const CARD_HEIGHT = 100
const HORIZONTAL_GAP = 100
const VERTICAL_GAP = 40

export function DagViewer({ workflow, run, className = '' }: DagViewerProps) {
  // Compute topological levels and layout coordinates
  const { nodes, edges, width, height } = useMemo(() => {
    const nodeMap = new Map(workflow.nodes.map((n) => [n.node_key, n]))
    const runNodeMap = new Map((run?.nodes || []).map((n) => [n.node_key, n]))

    // In-degree & adjacency list
    const inDegree: Record<string, number> = {}
    const adj: Record<string, string[]> = {}
    const revAdj: Record<string, string[]> = {}

    workflow.nodes.forEach((n) => {
      inDegree[n.node_key] = 0
      adj[n.node_key] = []
      revAdj[n.node_key] = []
    })

    workflow.edges.forEach((e) => {
      if (adj[e.from_node_key] && inDegree[e.to_node_key] !== undefined) {
        adj[e.from_node_key].push(e.to_node_key)
        revAdj[e.to_node_key].push(e.from_node_key)
        inDegree[e.to_node_key] = (inDegree[e.to_node_key] || 0) + 1
      }
    })

    // Compute levels (longest path from roots)
    const levels: Record<string, number> = {}
    const queue: string[] = []

    workflow.nodes.forEach((n) => {
      if (inDegree[n.node_key] === 0) {
        levels[n.node_key] = 0
        queue.push(n.node_key)
      }
    })

    // In case of cycles or disconnected components, fallback
    const visited = new Set<string>()
    while (queue.length > 0) {
      const u = queue.shift()!
      visited.add(u)
      const currentLevel = levels[u] || 0

      for (const v of adj[u] || []) {
        levels[v] = Math.max(levels[v] || 0, currentLevel + 1)
        queue.push(v)
      }
    }

    // Assign any unvisited nodes
    workflow.nodes.forEach((n) => {
      if (levels[n.node_key] === undefined) {
        levels[n.node_key] = 0
      }
    })

    // Group nodes by level
    const levelGroups: Record<number, string[]> = {}
    let maxLevel = 0
    let maxNodesInLevel = 0

    Object.entries(levels).forEach(([key, lvl]) => {
      if (!levelGroups[lvl]) levelGroups[lvl] = []
      levelGroups[lvl].push(key)
      maxLevel = Math.max(maxLevel, lvl)
    })

    Object.values(levelGroups).forEach((group) => {
      maxNodesInLevel = Math.max(maxNodesInLevel, group.length)
    })

    // Calculate layout coordinates
    const layoutNodes: Record<string, NodeLayout> = {}
    const totalW = (maxLevel + 1) * (CARD_WIDTH + HORIZONTAL_GAP) + 80
    const totalH = Math.max(300, maxNodesInLevel * (CARD_HEIGHT + VERTICAL_GAP) + 80)

    Object.entries(levelGroups).forEach(([lvlStr, keys]) => {
      const lvl = Number(lvlStr)
      const levelHeight = keys.length * CARD_HEIGHT + (keys.length - 1) * VERTICAL_GAP
      const startY = (totalH - levelHeight) / 2

      keys.forEach((key, idx) => {
        const def = nodeMap.get(key)
        const runState = runNodeMap.get(key)

        const x = 40 + lvl * (CARD_WIDTH + HORIZONTAL_GAP)
        const y = startY + idx * (CARD_HEIGHT + VERTICAL_GAP)

        layoutNodes[key] = {
          key,
          x,
          y,
          level: lvl,
          taskType: def?.task_type || 'task',
          status: runState?.status || 'PENDING',
          taskId: runState?.task_id || null,
          startedAt: runState?.started_at || null,
          finishedAt: runState?.finished_at || null,
          errorMessage: runState?.error_message || null,
        }
      })
    })

    // Build edge coordinate paths
    const layoutEdges = workflow.edges
      .map((e) => {
        const fromNode = layoutNodes[e.from_node_key]
        const toNode = layoutNodes[e.to_node_key]
        if (!fromNode || !toNode) return null

        const startX = fromNode.x + CARD_WIDTH
        const startY = fromNode.y + CARD_HEIGHT / 2
        const endX = toNode.x
        const endY = toNode.y + CARD_HEIGHT / 2

        const midX = (startX + endX) / 2
        const path = `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`

        // Color edge based on source status
        const isCompleted = fromNode.status === 'SUCCESS'
        const isRunning = fromNode.status === 'RUNNING'
        const isFailed = fromNode.status === 'FAILED'

        return {
          id: `${e.from_node_key}->${e.to_node_key}`,
          path,
          isCompleted,
          isRunning,
          isFailed,
        }
      })
      .filter(Boolean)

    return {
      nodes: Object.values(layoutNodes),
      edges: layoutEdges,
      width: totalW,
      height: totalH,
    }
  }, [workflow, run])

  return (
    <div className={`relative overflow-x-auto rounded-xl border border-slate-200/80 bg-slate-950 p-4 shadow-inner ${className}`}>
      {/* SVG Canvas for Connectors */}
      <svg
        width={width}
        height={height}
        className="block min-w-full"
        style={{ minHeight: `${height}px` }}
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="6"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#64748b" />
          </marker>
          <marker
            id="arrow-success"
            viewBox="0 0 10 10"
            refX="6"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#10b981" />
          </marker>
          <marker
            id="arrow-running"
            viewBox="0 0 10 10"
            refX="6"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#0ea5e9" />
          </marker>
        </defs>

        {/* Directed Edges */}
        {edges.map((e) => {
          if (!e) return null
          let strokeColor = '#334155'
          let markerId = 'arrow'
          let dashArray = 'none'

          if (e.isCompleted) {
            strokeColor = '#10b981'
            markerId = 'arrow-success'
          } else if (e.isRunning) {
            strokeColor = '#0ea5e9'
            markerId = 'arrow-running'
            dashArray = '5,5'
          } else if (e.isFailed) {
            strokeColor = '#f43f5e'
          }

          return (
            <g key={e.id}>
              <path
                d={e.path}
                fill="none"
                stroke={strokeColor}
                strokeWidth={e.isCompleted || e.isRunning ? 2.5 : 1.5}
                strokeDasharray={dashArray}
                markerEnd={`url(#${markerId})`}
                className={e.isRunning ? 'animate-pulse' : ''}
              />
            </g>
          )
        })}

        {/* HTML Node Foreign Objects */}
        {nodes.map((n) => {
          const isRunning = n.status === 'RUNNING'
          const isSuccess = n.status === 'SUCCESS'
          const isFailed = n.status === 'FAILED'

          let borderClass = 'border-slate-800 bg-slate-900/90 text-slate-200'
          if (isRunning) borderClass = 'border-sky-500 bg-slate-900 text-sky-200 ring-2 ring-sky-500/20'
          else if (isSuccess) borderClass = 'border-emerald-700/60 bg-slate-900 text-emerald-100'
          else if (isFailed) borderClass = 'border-rose-600 bg-slate-900 text-rose-200'

          return (
            <foreignObject
              key={n.key}
              x={n.x}
              y={n.y}
              width={CARD_WIDTH}
              height={CARD_HEIGHT}
              className="overflow-visible"
            >
              <div
                className={`flex h-full w-full flex-col justify-between rounded-xl border p-3 shadow-lg backdrop-blur-xs transition-all ${borderClass}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-bold text-xs text-white">
                    {n.key}
                  </span>
                  <Badge status={n.status} size="sm" />
                </div>

                <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono">
                  <span>{n.taskType}</span>
                  {n.taskId ? (
                    <Link
                      to={`/tasks/${n.taskId}`}
                      className="text-cyan-400 hover:underline hover:text-cyan-300"
                      title="Inspect underlying task"
                    >
                      {n.taskId.slice(0, 6)}…
                    </Link>
                  ) : (
                    <span className="text-slate-600">—</span>
                  )}
                </div>

                <div className="text-[10px] text-slate-500 truncate">
                  {n.errorMessage ? (
                    <span className="text-rose-400">{n.errorMessage}</span>
                  ) : n.finishedAt ? (
                    <span>Done: {formatDate(n.finishedAt)}</span>
                  ) : n.startedAt ? (
                    <span className="text-sky-400">Started: {formatDate(n.startedAt)}</span>
                  ) : (
                    <span>Awaiting dispatch</span>
                  )}
                </div>
              </div>
            </foreignObject>
          )
        })}
      </svg>
    </div>
  )
}
