"""把一帧里同色的竖线（按格子边界）串成尽量少的 AirCrush：竖着只走要画的线，换列只走纯横线（0x 里看不见）。

把每个高度（格子边界）当成一个点，每条竖线当成连接两个高度的边；同一高度上的任意两列之间可以随意横跳。
所需笔数 = 每个连通块 max(1, 奇数度点数 / 2)。用 Hierholzer 算法求路径。
"""
from __future__ import annotations

from collections import defaultdict


def edges_h(rows: int, htop: int) -> list[int]:
    """格子边界的 h：rows 个格子 -> rows+1 个边界，0..htop（整数）。第 r 行（0 = 最上）在 [E[rows-1-r], E[rows-r]]。"""
    return [round(htop * k / rows) for k in range(rows + 1)]


def column_runs(color_grid, rows: int, cols: int, color: int):
    """[(col, lo_level, hi_level)]：第 col 列里颜色为 color 的连续格子，lo/hi 为边界编号（0 = 最下）。"""
    out = []
    for j in range(cols):
        r = 0
        while r < rows:
            if color_grid[r][j] == color:
                r0 = r
                while r + 1 < rows and color_grid[r + 1][j] == color:
                    r += 1
                out.append((j, rows - 1 - r, rows - r0))   # 下边界、上边界
            r += 1
    return out


def trails(runs) -> list[list[tuple[int, int, int]]]:
    """把竖线分成最少条路径。返回 [[(col, from_level, to_level), ...], ...]（按走的方向）。"""
    if not runs:
        return []
    adj = defaultdict(list)            # level -> [(edge_id, other_level)]
    for e, (_, a, b) in enumerate(runs):
        adj[a].append((e, b))
        adj[b].append((e, a))
    V = -1                             # 虚拟点：连到所有奇数度的高度，使每个连通块都有欧拉回路
    nv = len(runs)
    odd = [v for v in adj if len(adj[v]) % 2]
    for k, v in enumerate(odd):
        adj[v].append((nv + k, V))
        adj[V].append((nv + k, v))
    used = set()
    ptr = defaultdict(int)
    out = []
    for start in ([V] if odd else []) + sorted(v for v in adj if v != V):
        if ptr[start] >= len(adj[start]) or all(e in used for e, _ in adj[start]):
            continue
        # Hierholzer：得到 (edge, from, to) 的回路
        stack, circ = [(start, None)], []
        while stack:
            v, e_in = stack[-1]
            while ptr[v] < len(adj[v]) and adj[v][ptr[v]][0] in used:
                ptr[v] += 1
            if ptr[v] == len(adj[v]):
                stack.pop()
                if e_in is not None:
                    circ.append(e_in)
            else:
                e, w = adj[v][ptr[v]]
                used.add(e)
                stack.append((w, (e, v, w)))
        circ.reverse()
        # 在虚拟边处切开
        cur = []
        for e, a, b in circ:
            if e >= nv:
                if cur:
                    out.append(cur)
                cur = []
            else:
                cur.append((runs[e][0], a, b))
        if cur:
            out.append(cur)
    assert sorted((c, min(a, b), max(a, b)) for t in out for c, a, b in t) == sorted(runs)
    return out
