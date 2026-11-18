"""link 模式：在 zero --double 的基础上，把同一条 TIL 上全部画面的同色笔画串成一个 AirCrush。

调度（哪帧用哪条 TIL、到场 / 退场、停放距离）和 park / zero 一样。区别只在存放区：

  每条 TIL、每种颜色只有一个 AirCrush（--link all），按 m = 0, 1, 2 … 依次走过这条 TIL 上的所有画面。
  画面 m 的笔画都在 X = m·Δ 的 0x 段里（竖线按格子边界，每条线来回各画一遍，和 zero --double 相同）。
  画面之间、色块之间的移动只走看不见的路线：
    - 屏幕外：h = H（远高于可见上限 220）。从顶边（h=220）竖直上下也在屏幕外。
    - 判定线后方：X = m·Δ − B。判定线后方只画很短一段（实测 < 20），B 取 2000 就完全不画。
  碰到顶边的色块：从 H 直接竖直下到顶边，画完回到顶边再上到 H。
  不碰顶边的色块（被另一种颜色包围）：
    在后方移动到入口点 → 5 tick 内从后方回到判定线上（“入口残线”）→ 画完 → 5 tick 退回后方（“出口残线”）。
    入口和出口是同一点（每个连通块是一个欧拉回路）。残线在判定线后方最多画 DB（< 20）长。
  --link top：不碰顶边的色块不走后方，各自单独一个 AirCrush（没有残线，AirCrush 多一些）。

同一条 TIL 的白、黑两条链在时间上先后错开（白画完再画黑），这样每个时刻的 X 只有一个值。
"""
from __future__ import annotations

from collections import defaultdict

from euler_paths import column_runs, edges_h

GRID = 5


def components(runs):
    """按高度连通把竖线分组：[(levels:set, runs:list)]。"""
    par = {}

    def f(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for _, a, b in runs:
        par[f(a)] = f(b)
    groups = defaultdict(list)
    for r in runs:
        groups[f(r[1])].append(r)
    return list(groups.values())


def circuit(runs, start, first=None, double=True):
    """欧拉回路：[(col, from, to)]，从高度 start 出发。
    double=True：每条竖线来回各走一遍（重边）；False：runs 本身就是要走的边（每个高度必须是偶数度）。
    first：指定第一笔走哪条竖线（必须以 start 为端点）。"""
    adj = defaultdict(list)
    edges = []
    if first is not None:
        runs = [first] + [r for r in runs if r is not first]
    for col, a, b in runs:
        for _ in range(2 if double else 1):
            e = len(edges)
            edges.append((col, a, b))
            adj[a].append((e, b))
            adj[b].append((e, a))
    used = [False] * len(edges)
    ptr = defaultdict(int)
    stack, out = [(start, None)], []
    while stack:
        v, e_in = stack[-1]
        while ptr[v] < len(adj[v]) and used[adj[v][ptr[v]][0]]:
            ptr[v] += 1
        if ptr[v] == len(adj[v]):
            stack.pop()
            if e_in is not None:
                out.append(e_in)
        else:
            e, w = adj[v][ptr[v]]
            used[e] = True
            stack.append((w, (edges[e][0], v, w)))
    out.reverse()
    assert len(out) == len(edges)
    return out


def gray_levels(img, rows: int, cols: int, lo: int = 85, hi: int = 170) -> list[list[int]]:
    """三档灰度：每格白线条数 w ∈ {0, 1, 2}（黑线条数 = 2 - w）。0 = 黑黑，1 = 白黑（灰），2 = 白白。
    为了让每种颜色都能一笔画成欧拉回路，每一行的灰格数必须是偶数：
    某行灰格数为奇数时，改动一个最接近档位分界的格子（误差最小）。"""
    out = []
    for r in range(rows):
        v = [img[r * cols + j] for j in range(cols)]
        w = [0 if x < lo else 1 if x < hi else 2 for x in v]
        if sum(x == 1 for x in w) % 2:
            best = None
            for j, x in enumerate(v):
                if w[j] == 1:          # 灰 -> 黑或白
                    for nw, cost in ((0, x - lo + 1), (2, hi - x)):
                        if best is None or cost < best[0]:
                            best = (cost, j, nw)
                else:                  # 黑/白 -> 灰
                    cost = (lo - x) if w[j] == 0 else (x - hi + 1)
                    if best is None or cost < best[0]:
                        best = (cost, j, 1)
            w[best[1]] = best[2]
        out.append(w)
    return out


def gray_edges(w, rows: int, cols: int, white: bool):
    """一种颜色要画的竖线（多重集）：第一层 = 至少 1 条的格子，第二层 = 2 条的格子，各按列取连续段。"""
    edges = []
    for j in range(cols):
        mult = [w[r][j] if white else 2 - w[r][j] for r in range(rows)]
        for need in (1, 2):
            r = 0
            while r < rows:
                if mult[r] >= need:
                    r0 = r
                    while r + 1 < rows and mult[r + 1] >= need:
                        r += 1
                    edges.append((j, rows - 1 - r, rows - r0))
                r += 1
    return edges


def build_link(args, sched, idxs, frames, black, hs, COLS, X_W, WHITE, BLACK, add, z, count, groups_img):
    """groups_img: [(til, m, img)]，按帧顺序。返回 (lines, storage_knots{til: [(t, X)]})。"""
    delta, B, H = float(args.spacing), float(args.behind), int(args.sky)
    rows = args.rows
    E = edges_h(rows, args.htop)
    TOP = rows
    keep = {"both": (WHITE, BLACK), "white": (WHITE,), "black": (BLACK,)}[args.colors]
    per_til = defaultdict(list)
    for til, m, img in groups_img:
        per_til[til].append((m, img))

    lines = []
    knots = {}
    for til, pics in per_til.items():
        kn = [(z, None)]                      # None：由调用方填 -far
        chain = {c: [] for c in keep}         # 主链控制点 [(t, x, w, h)]
        t = z + GRID

        def emit(pts_list, x, w, h, X):
            nonlocal t
            pts_list.append((t, x, w, h))
            kn.append((t, X))
            t += GRID

        for m, img in pics:
            base = m * delta
            gray = getattr(args, "gray", False)
            if gray:
                glv = gray_levels(img, rows, COLS)
            else:
                grid = [[WHITE if img[r * COLS + j] >= args.threshold else BLACK for j in range(COLS)]
                        for r in range(rows)]
            dbl = not gray
            for c in keep:
                edges = gray_edges(glv, rows, COLS, c == WHITE) if gray else column_runs(grid, rows, COLS, c)
                comps = components(edges)
                comps.sort(key=lambda g: TOP not in {l for _, a, b in g for l in (a, b)})  # 碰顶边的先画
                for g in comps:
                    levels = {l for _, a, b in g for l in (a, b)}
                    top = TOP in levels
                    if not top and args.link == "top":
                        own = []                          # 单独一个 AirCrush，全在 0x 里
                        cir = circuit(g, min(levels), double=dbl)
                        prev = None
                        for col, a, b in cir:
                            x, w = X_W[col]
                            if prev != (x, w, E[a]):
                                emit(own, x, w, E[a], base)
                            emit(own, x, w, E[b], base)
                            prev = (x, w, E[b])
                        lines.append((c, til, own))
                        continue
                    if top:
                        start, first = TOP, None
                    elif args.stub_at == "top":
                        # 残线朝玩家侧，屏幕上往下延伸：放在色块最上沿、最高的那一列，让它落在色块自己身上
                        start = max(levels)
                        first = max((r for r in g if r[2] == start), key=lambda r: r[2] - r[1])
                    else:
                        start, first = min(levels), None
                    cir = circuit(g, start, first, double=dbl)
                    pts = chain[c]
                    x0, w0 = X_W[cir[0][0]]
                    if top:
                        emit(pts, x0, w0, H, base)            # 屏幕外，竖直下到顶边
                    else:
                        emit(pts, x0, w0, H, base - B)        # 在判定线后方（不画）就位
                        emit(pts, x0, w0, E[start], base - B)
                    prev = None
                    for col, a, b in cir:
                        x, w = X_W[col]
                        if prev != (x, w, E[a]):
                            emit(pts, x, w, E[a], base)       # 入口：从后方 5 tick 回到判定线（或从顶边下来）
                        emit(pts, x, w, E[b], base)
                        prev = (x, w, E[b])
                    xl, wl, hl = prev
                    if top:
                        emit(pts, xl, wl, H, base)            # 回到屏幕外
                    else:
                        emit(pts, xl, wl, hl, base - B)       # 出口：5 tick 退回后方
                        emit(pts, xl, wl, H, base - B)
        for c in keep:
            if len(chain[c]) >= 2:
                lines.append((c, til, chain[c]))
        kn.append((t, count[til] * delta))
        knots[til] = kn
    return lines, knots
