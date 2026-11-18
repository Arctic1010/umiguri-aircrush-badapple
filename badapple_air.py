r"""视频 -> AirCrush 装饰线帧动画（UMIGURI / Margrete）。原理与实测规律见 docs/NOTES.md。

默认参数就是最终方案（link top，31x25 黑白，h 0..240，15 fps）：
    python badapple_air.py --video badapple.mp4                       # 只预览前 5 秒，写 badapple_preview.png
    python badapple_air.py --video badapple.mp4 --seconds 232.396 --write --instance 1234   # 写入整首

--instance 是 Margrete 里「扩展功能 → Margrete RPC」显示的 4 位实例 ID。目标谱面必须是空的、BPM 138。

时间轴调度（--mode）
    link（默认）：TIL 1..63 轮流，帧 i 放在 TIL[i % 63]。每幅画面整个放在 0x 段里；
        同一 TIL 同色的笔画用欧拉回路串成一个 AirCrush，换列走纯横线，换画面经屏幕上方（h > 240）绕过去。
        被另一种颜色包围、碰不到顶边的色块单独一个 AirCrush（--link top）。
    zero：同样放在 0x 里，每幅画面每色单独几个 AirCrush（AirCrush 更多）。
    park：每条线一个 AirCrush、1x 点段（最早的无瑕疵方案，45 万个 AirCrush，很卡）。
    overlap / behind / emerge / store / pertil：早期失败方案，仅保留作对比。

帧时刻：S_i = start + 5*round(i * 220.8 / fps)（BPM 138：1 秒 = 1104 tick = 220.8 个 1/384 格）
取视频帧：round(((S_i - anchor)/1104 - AUDIO_LAG) * 30)，负数 = 片头黑场（--audio-lag，默认 0.2629 s）。
视频最后一帧放一个 Damage 音符，否则 UMIGURI 会提前结束画面。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

from aircrush_kit import GRID, center_to_xw

VIDEO = Path(os.environ.get("BADAPPLE_VIDEO", "badapple.mp4"))   # 用 --video 或环境变量 BADAPPLE_VIDEO 指定
VIDEO_FPS = 30
CROP = "crop=1440:1080:240:0"   # 1920x1080 中的 4:3 内容区（实测）
SCREEN_TOP = 240                 # 判定线上屏幕上沿对应的 h（实测，test_calib.py）


def crop_for(htop: int) -> str:
    """保持比例：31 列 × (0..htop) 的格子对应的视频区域。实测 htop=220 时正好是 4:3（1440x1080）。
    htop 更高时，宽高比 = 4/3 * 220/htop，从 4:3 区域中间左右裁掉一些。"""
    w = round(1080 * 4 / 3 * 220 / htop / 2) * 2
    if w >= 1440:
        return CROP
    return f"crop={w}:1080:{240 + (1440 - w) // 2}:0"
AUDIO_LAG = 0.2629              # 谱面 ogg 比视频晚 0.2629 s（互相关实测）
BPM = 138
TPS = BPM * 8                   # ticks per second = 1104
COLS = 31                       # c = 0.5, 1.0, ..., 15.5
WHITE, BLACK = 10, 11           # Margrete 颜色编号（ID 0954 第 23 小节实测）
X_W = [center_to_xw(Fraction(1, 2) + Fraction(j, 2)) for j in range(COLS)]

# 一条线：(P, (x0, w0, h0), (x1, w1, h1), color, til)
Chain = tuple  # (color, til, [(t, x, w, h), ...])：一个 AirCrush 的全部控制点
Line = tuple[int, tuple[int, int, int], tuple[int, int, int], int, int]


def frame_schedule(start: int, seconds: float, fps: float) -> list[int]:
    grids_per_frame = Fraction(TPS, GRID) / Fraction(fps).limit_denominator(1000)
    n = int(seconds * fps)
    return [start + GRID * round(i * grids_per_frame) for i in range(n + 1)]


def video_index(tick: int, anchor: int) -> int:
    return round(((tick - anchor) / TPS - AUDIO_LAG) * VIDEO_FPS)


def load_frames(first: int, last: int, rows: int, crop: str = CROP) -> dict[int, bytes]:
    first = max(first, 0)
    vf = f"select='between(n\\,{first}\\,{last})',{crop},scale={COLS}:{rows}:flags=area,format=gray"
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(VIDEO), "-vf", vf, "-vsync", "0",
         "-frames:v", str(last - first + 1), "-f", "rawvideo", "-"],
        check=True, capture_output=True,
    ).stdout
    size = COLS * rows
    return {first + k: raw[k * size:(k + 1) * size] for k in range(len(raw) // size)}


def _runs(cells: list[int]) -> list[tuple[int, int, int]]:
    """[color...] -> [(color, k0, k1)] 同色连续段（含端点）。"""
    out, k0 = [], 0
    for k in range(1, len(cells) + 1):
        if k == len(cells) or cells[k] != cells[k0]:
            out.append((cells[k0], k0, k - 1))
            k0 = k
    return out


def frame_lines(img: bytes, p: int, til: int, hs: list[int], args) -> list[Line]:
    rows = len(hs)
    color = [[WHITE if img[r * COLS + j] >= args.threshold else BLACK for j in range(COLS)]
             for r in range(rows)]
    keep = {"both": (WHITE, BLACK), "white": (WHITE,), "black": (BLACK,)}[args.colors]
    out: list[Line] = []
    if args.merge == "none":
        for r in range(rows):
            for j in range(COLS):
                if color[r][j] in keep:
                    x, w = X_W[j]
                    out.append((p, (x, w, hs[r]), (x, w, hs[r]), color[r][j], til))
    elif args.merge == "rows":
        for r in range(rows):
            for col, j0, j1 in _runs(color[r]):
                if col in keep:
                    out.append((p, (*X_W[j0], hs[r]), (*X_W[j1], hs[r]), col, til))
    else:  # cols
        for j in range(COLS):
            for col, r0, r1 in _runs([color[r][j] for r in range(rows)]):
                if col in keep:
                    x, w = X_W[j]
                    out.append((p, (x, w, hs[r0]), (x, w, hs[r1]), col, til))
    return out


def build(args):
    sched = frame_schedule(args.start, args.seconds, args.fps)
    if any(b - a < 2 * GRID for a, b in zip(sched, sched[1:])):
        raise SystemExit("帧间隔太短（至少 10 tick）")
    idxs = [video_index(s, args.anchor) for s in sched[:-1]]
    crop = crop_for(args.htop) if getattr(args, "aspect", "stretch") == "crop" else CROP
    frames = load_frames(min(idxs), max(idxs), args.rows, crop)
    black = bytes(COLS * args.rows)
    hs = [round(args.htop * (args.rows - 1 - r) / (args.rows - 1)) for r in range(args.rows)]

    if args.mode == "store":
        return build_store(args, sched, idxs, frames, black, hs)
    if args.mode == "pertil":
        return build_pertil(args, sched, idxs, frames, black, hs)
    if args.mode == "park":
        return build_park(args, sched, idxs, frames, black, hs)
    if args.mode in ("zero", "link"):
        return build_zero(args, sched, idxs, frames, black, hs)

    lines: list[Line] = []
    events: dict[int, dict[int, float]] = defaultdict(dict)   # til -> {tick: speed}
    pics = []
    for i, (s0, s1) in enumerate(zip(sched, sched[1:])):
        til = args.tils[i % len(args.tils)]
        img = frames.get(idxs[i], black)
        pics.append(img)
        ev = events[til]
        if s0 - GRID >= 0:                     # 到场
            ev[s0 - GRID] = -args.jump if args.mode == "emerge" else args.jump
        ev[s0] = 0.0                           # 定格
        if args.mode == "overlap":
            ev[s1] = args.dot                  # 点段：下一帧已到位后才被吃掉（会有 5 tick 叠影）
            ev[s1 + GRID] = 0.0
            p = s1
        else:  # behind / emerge：旧画面瞬间退到判定线后方，在看不见的地方被吃掉
            r = args.remove if args.remove else (args.jump if args.mode == "emerge" else args.jump / 2)
            ev[s1] = r                         # 退场：X 升 5r，画面 i 瞬间到判定线后面
            ev[s1 + GRID] = -(r - args.dot)    # 回落到点段起点（画面仍在判定线后面）
            ev[s1 + 2 * GRID] = -args.dot      # 点段（负速度：剩余部分始终在判定线后面）
            ev[s1 + 3 * GRID] = 0.0
            p = s1 + 2 * GRID
        lines += frame_lines(img, p, til, hs, args)
    for til in args.tils:
        events[til].setdefault(args.start, 0.0)
    return sched, idxs, pics, lines, events


def knots_to_events(knots: list[tuple[int, float]]) -> dict[int, float]:
    """卷动量折线 [(tick, X)] -> 速度事件 {tick: speed}（每段匀速，最后一段之后速度 0）。"""
    ev = {}
    for (ta, xa), (tb, xb) in zip(knots, knots[1:]):
        assert tb > ta, (ta, tb)
        ev[ta] = (xb - xa) / (tb - ta)
    ev[knots[-1][0]] = 0.0
    return ev


def compress_events(ev: dict[int, float]) -> dict[int, float]:
    """去掉和前一个事件速度相同的事件（不改变卷动）。"""
    out, prev = {}, None
    for t in sorted(ev):
        if prev is None or ev[t] != prev:
            out[t] = ev[t]
            prev = ev[t]
    return out


def build_store(args, sched, idxs, frames, black, hs):
    """store 模式：所有画面存放在视频结束之后，视频期间没有任何画面被判定线吃掉。

    每条 TIL 的卷动量 X（Δ = 5*JUMP，r = 5*REMOVE，L_m = m*Δ 为该 TIL 第 m 幅画面的位置）：
        到场   [S_i-5, S_i]     L_m - Δ + r -> L_m     画面 m 从 Δ-r 远处飞到判定线
        显示   [S_i, S_{i+1}]   L_m（0x）               画面 m 深度 [0, 5*DOT]，其余画面在 >Δ-r 远处或判定线后方
        退场   [S_{i+1}, +5]    L_m -> L_m + r          画面 m 退到判定线后方（未吃掉的音符在后方不画）
        存放区 Z 之后           回到 L_0，再逐幅经过：画面 m 的点段 [Q_m, Q_m+5] 速度 DOT，Q_m = Z+5+10m
    """
    delta, r, dot = 5 * args.jump, 5 * args.remove, args.dot
    if r <= 5 * dot:
        raise SystemExit("--remove 太小：退场距离必须大于点的长度")
    per_til: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    pics = []
    for i, (s0, s1) in enumerate(zip(sched, sched[1:])):
        per_til[args.tils[i % len(args.tils)]].append((i, s0, s1))
        pics.append(frames.get(idxs[i], black))

    z = sched[-1] + 4 * GRID + args.store_gap
    z -= z % GRID
    lines: list[Line] = []
    events: dict[int, dict[int, float]] = {}
    for til, items in per_til.items():
        knots: list[tuple[int, float]] = []

        def add(t: int, x: float) -> None:
            if knots and knots[-1][0] == t:
                assert abs(knots[-1][1] - x) < 1e-9, (til, t, knots[-1][1], x)
                return
            knots.append((t, x))

        for m, (i, s0, s1) in enumerate(items):
            lm = m * delta
            if not (i == 0 and s0 == args.start):
                add(max(args.start, s0 - GRID) if m == 0 else s0 - GRID, lm - delta + r)
                add(s0 - GRID, lm - delta + r)
            add(s0, lm)
            add(s1, lm)
            add(s1 + GRID, lm + r)
        add(z, knots[-1][1])
        for m, (i, _, _) in enumerate(items):
            q = z + GRID + 2 * GRID * m
            add(q, m * delta)
            add(q + GRID, m * delta + GRID * dot)
            if m + 1 < len(items):
                add(q + 2 * GRID, (m + 1) * delta)
            lines += frame_lines(pics[i], q, til, hs, args)
        if knots[0][0] > 0:                    # 区间之前保持静止，避免默认 1x 让画面提前飘过来
            knots.insert(0, (0, knots[0][1]))
        events[til] = knots_to_events(knots)
    return sched, idxs, pics, lines, events


def build_pertil(args, sched, idxs, frames, black, hs):
    """pertil 模式：每幅画面独占一条 TIL（编号 til_base + i），画面只在“远处 <-> 判定线”之间移动。

    第 i 幅画面所在 TIL 的卷动量 X（D = --far，点段 Q 为所有 TIL 共用的存放位置）：
        0 .. S_i-5        X = 0          画面在 D 远处（不可见）
        [S_i-5, S_i]      0 -> D         到场：从远处飞到判定线
        [S_i, S_{i+1}]    D（0x）        显示：深度 [0, 5*DOT]
        [S_{i+1}, +5]     D -> 0         退场：飞回 D 远处（朝远方，不经过判定线）
        [Q-5, Q]          0 -> D         视频结束后：所有画面同时到判定线
        [Q, Q+5]          DOT            点段（被吃掉）
        [Q+5, Q+10]       +D             已吃掉的部分推到玩家身后 D 处，不再可见
    视频期间：没有画面被吃掉，没有画面进入判定线后方，也没有其他画面扫过。
    """
    far, dot = float(args.far), args.dot
    q = sched[-1] + 4 * GRID + args.store_gap
    q -= q % GRID
    lines: list[Line] = []
    events: dict[int, dict[int, float]] = {}
    pics = []
    for i, (s0, s1) in enumerate(zip(sched, sched[1:])):
        til = args.til_base + i
        img = frames.get(idxs[i], black)
        pics.append(img)
        knots: list[tuple[int, float]] = []
        if s0 - GRID >= 0:
            knots += [(0, 0.0), (s0 - GRID, 0.0)] if s0 - GRID > 0 else [(0, 0.0)]
        knots += [(s0, far), (s1, far), (s1 + GRID, 0.0),
                  (q - GRID, 0.0), (q, far), (q + GRID, far + GRID * dot), (q + 2 * GRID, 2 * far + GRID * dot)]
        if knots[0][0] != 0:
            knots.insert(0, (0, knots[0][1]))
        events[til] = knots_to_events(knots)
        lines += frame_lines(img, q, til, hs, args)
    args.tils = list(range(args.til_base, args.til_base + len(pics)))
    return sched, idxs, pics, lines, events


def build_park(args, sched, idxs, frames, black, hs):
    """park 模式（推荐）：TIL 1..63 轮流，帧 i 用 TIL[i % K]，是该 TIL 的第 m = i // K 幅画面。

    存放：视频结束后的存放区里，第 m 幅画面的点段在 Q_m，且 X(Q_m) = m*Δ（Δ = --spacing）。
    视频期间每条 TIL 的卷动量 X 只取两种值：
        停放  X = -D（D = --far）  该 TIL 全部画面在前方 >= D 远处（不可见）
        显示  X = m*Δ（0x）       画面 m 深度 [0, 5*DOT]；同 TIL 其他画面在 >= Δ 前方或 >= Δ 后方
    切换都只用 5 tick、速度约 (mΔ+D)/5：
        到场 [S_i-5, S_i]      -D -> mΔ   画面 m 从远处飞来
        退场 [S_{i+1}, +5]     mΔ -> -D   画面 m 飞回远处（朝前方，不进入判定线后方）
    视频期间没有任何画面被判定线吃掉；经过可视范围/判定线附近的画面都以 >= D/5 每 tick 的速度掠过。
    存放区：所有 TIL 同步依次吃掉第 0, 1, 2… 幅（每幅 10 tick），被吃掉的部分留在 >= Δ 后方。
    """
    K = len(args.tils)
    delta, far, dot = float(args.spacing), float(args.far), args.dot
    if delta <= 5 * dot:
        raise SystemExit("--spacing 太小")
    z = sched[-1] + 2 * GRID + args.store_gap
    z -= z % GRID
    lines: list[Line] = []
    knots: dict[int, list[tuple[int, float]]] = {til: [] for til in args.tils}
    pics = []
    count = defaultdict(int)

    def add(til: int, t: int, x: float) -> None:
        kn = knots[til]
        if not kn:
            if t > 0:
                kn.append((0, -far))      # 开始前停在远处
            kn.append((t, x))
            return
        if kn[-1][0] == t:
            assert abs(kn[-1][1] - x) < 1e-9, (til, t)
            return
        assert t > kn[-1][0], (til, t, kn[-1])
        kn.append((t, x))

    slots: dict[int, list[int]] = defaultdict(list)   # til -> 每幅画面占用的槽数
    groups = []                                       # (til, m, 该帧的线)
    for i, (s0, s1) in enumerate(zip(sched, sched[1:])):
        til = args.tils[i % K]
        m = i // K
        count[til] = m + 1
        img = frames.get(idxs[i], black)
        pics.append(img)
        if s0 - GRID > 0:
            add(til, s0 - GRID, -far)
        add(til, s0, m * delta)
        add(til, s1, m * delta)
        add(til, s1 + GRID, -far)
        fl = frame_lines(img, 0, til, hs, args)
        groups.append((til, m, fl))
        if args.chain:
            per = max(sum(l[3] == c for l in fl) for c in (WHITE, BLACK))
            slots[til].append(max(1, min(args.chain, per)))
        else:
            slots[til].append(1)

    # 存放区：TIL 上第 m 幅画面占 [q_m, q_m + 10*n_m)，共 n_m 个槽。槽 k 的线段在 [q+10k, q+10k+5]，
    # 槽与槽之间的 5 tick 是 0x 连接段（沿轨道方向长度 0，看不见）。
    # 偶数槽速度 +dot（深度 0 -> 5dot），奇数槽 -dot（深度 5dot -> 0），所以每条线都在深度 [0, 5dot]，
    # 和不串链时完全一样。白、黑两种颜色的链并排放在同样的槽里。
    qs: dict[int, list[int]] = {}
    for til, ns in slots.items():
        q, out = z + GRID, []
        for n in ns:
            out.append(q)
            q += 2 * GRID * n
        qs[til] = out + [q]
    for til, m, fl in groups:
        q = qs[til][m]
        if not args.chain:
            lines += [(c, til, [(q, *a), (q + GRID, *b)]) for _, a, b, c, _ in fl]
            continue
        for c in (WHITE, BLACK):
            mine = [l for l in fl if l[3] == c]
            for j0 in range(0, len(mine), args.chain):
                pts = []
                for k, (_, a, b, _, _) in enumerate(mine[j0:j0 + args.chain]):
                    t = q + 2 * GRID * k
                    pts += [(t, *a), (t + GRID, *b)] if k % 2 == 0 else [(t, *b), (t + GRID, *a)]
                lines.append((c, til, pts))

    for til in args.tils:
        if til not in count:
            continue
        add(til, z, -far)
        for m, n in enumerate(slots[til]):
            q = qs[til][m]
            for k in range(n):
                near, far_end = m * delta, m * delta + GRID * dot
                add(til, q + 2 * GRID * k, far_end if k % 2 else near)
                add(til, q + 2 * GRID * k + GRID, near if k % 2 else far_end)
        add(til, qs[til][-1], count[til] * delta)
    events = {til: knots_to_events(kn) for til, kn in knots.items() if til in count}
    return sched, idxs, pics, lines, events


def build_zero(args, sched, idxs, frames, black, hs):
    """zero 模式：调度和 park 一样，但每幅画面整个放在 0x 段里（不再需要 1x 点段）。

    实测（test_zero.py）：0x 里竖线、斜线和 1x 一样清楚，纯横线看不见。所以：
      - 每列同色的连续格子画成一条竖线，从格子下边界到上边界（单格也有高度，不会消失）；
      - 一帧里同色的竖线用欧拉路径串成尽量少的 AirCrush：竖着只走要画的线，换列只走纯横线。
    --double：每条线来回各画一遍（全部两层，透明度均匀），每个连通块一笔，AirCrush 更少。
    存放区第 m 幅画面占 [q, q + 5(P-1)]（P = 该帧最长一笔的控制点数），X 恒为 m*Δ。
    """
    from euler_paths import column_runs, edges_h, trails
    K = len(args.tils)
    delta, far = float(args.spacing), float(args.far)
    E = edges_h(args.rows, args.htop)
    z = sched[-1] + 2 * GRID + args.store_gap
    z -= z % GRID
    knots: dict[int, list[tuple[int, float]]] = {til: [] for til in args.tils}
    pics, groups, count = [], [], defaultdict(int)
    keep = {"both": (WHITE, BLACK), "white": (WHITE,), "black": (BLACK,)}[args.colors]

    def add(til: int, t: int, x: float) -> None:
        kn = knots[til]
        if not kn:
            if t > 0:
                kn.append((0, -far))
            kn.append((t, x))
            return
        if kn[-1][0] == t:
            assert abs(kn[-1][1] - x) < 1e-9, (til, t)
            return
        assert t > kn[-1][0], (til, t, kn[-1])
        kn.append((t, x))

    for i, (s0, s1) in enumerate(zip(sched, sched[1:])):
        til = args.tils[i % K]
        m = i // K
        count[til] = m + 1
        img = frames.get(idxs[i], black)
        pics.append(img)
        if s0 - GRID > 0:
            add(til, s0 - GRID, -far)
        add(til, s0, m * delta)
        add(til, s1, m * delta)
        add(til, s1 + GRID, -far)
        if args.mode == "link":
            groups.append((til, m, img))
            continue
        grid = [[WHITE if img[r * COLS + j] >= args.threshold else BLACK for j in range(COLS)]
                for r in range(args.rows)]
        paths = []
        for c in keep:
            runs = column_runs(grid, args.rows, COLS, c)
            for tr in trails(runs + runs if args.double else runs):
                pts = []
                for col, a, b in tr:
                    x, w = X_W[col]
                    if not pts or pts[-1][1:3] != (x, w) or pts[-1][3] != E[a]:
                        pts.append([x, w, E[a]])          # 横跳到新的一列（或第一笔起点）
                    pts.append([x, w, E[b]])
                paths.append((c, til, pts))
        groups.append((til, m, paths))

    if args.mode == "link":
        from zero_link import build_link
        lines, store = build_link(args, sched, idxs, frames, black, hs, COLS, X_W, WHITE, BLACK,
                                  add, z, count, groups)
        for til, kn in store.items():
            for t, x in kn:
                add(til, t, -far if x is None else x)
        events = {til: compress_events(knots_to_events(kn)) for til, kn in knots.items() if til in count}
        return sched, idxs, pics, lines, events

    lines = []
    q_of: dict[int, int] = {}
    for til, m, paths in groups:            # 同一 TIL 的画面按 m 递增出现
        if til not in q_of:
            add(til, z, -far)               # 视频结束后先停在远处
            q_of[til] = z + GRID
        q = q_of[til]
        P = max(len(p) for _, _, p in paths)
        for c, _, pts in paths:
            lines.append((c, til, [(q + GRID * k, x, w, h) for k, (x, w, h) in enumerate(pts)]))
        add(til, q, m * delta)                    # 整幅画面在 0x 里，X 恒为 m*Δ
        add(til, q + GRID * (P - 1), m * delta)
        q_of[til] = q + GRID * P                  # 下一幅：5 tick 内跳到 (m+1)*Δ
    for til, q in q_of.items():
        add(til, q, count[til] * delta)
    events = {til: knots_to_events(kn) for til, kn in knots.items() if til in count}
    return sched, idxs, pics, lines, events


def as_chains(lines) -> list:
    """旧模式的 Line 列表 -> Chain 列表（每个 AirCrush 两个控制点）。park 模式本来就返回 Chain。"""
    if not lines or len(lines[0]) == 3:
        return lines
    return [(c, til, [(p, *a), (p + GRID, *b)]) for p, a, b, c, til in lines]


def validate(chains, events, sched) -> None:
    for _, til, pts in chains:
        assert 0 <= til < 1000 and len(pts) >= 2
        for (t0, *_), (t1, *_) in zip(pts, pts[1:]):
            assert t1 > t0, ("控制点时间不严格递增", t0, t1)
        for t, x, w, h in pts:
            assert t >= 0 and t % GRID == 0, t       # 全部在 1/384 网格
            assert w >= 1 and x >= 0 and x + w <= 16 and h >= 0, (x, w, h)
    for til, ev in events.items():
        assert all(t % GRID == 0 for t in ev), f"TIL{til} 速度事件不在网格"


def preview_png(pics, rows: int, path: Path, scale: int = 6, per_row: int = 10) -> None:
    pics = pics[:: max(1, len(pics) // 30)][:30]
    tw, th = COLS * scale + 4, rows * scale + 4
    W, H = tw * per_row, th * ((len(pics) + per_row - 1) // per_row)
    buf = bytearray([128]) * (W * H)
    for k, img in enumerate(pics):
        ox, oy = (k % per_row) * tw + 2, (k // per_row) * th + 2
        for y in range(rows * scale):
            for x in range(COLS * scale):
                buf[(oy + y) * W + ox + x] = 255 if img[(y // scale) * COLS + x // scale] >= 128 else 0
    pgm = path.with_suffix(".pgm")
    pgm.write_bytes(f"P5 {W} {H} 255\n".encode() + bytes(buf))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(pgm), str(path)], check=True)
    pgm.unlink()


def write(args, lines: list[Line], events, lo: int, hi: int) -> None:
    from margrete_rpc import Margrete
    from margrete_rpc.chart.events import TimelineSpeedEvent
    from margrete_rpc.chart.notes import R

    tils = sorted(set(args.tils) | set(args.clear_tils))
    lookahead = hi + 20000   # 事件扫描默认只到最后一个音符 +19200，这里扫够整个区间
    with Margrete(args.instance, timeout=900) as m:
        # 一次扫描太多 TIL 会让 Margrete 超时：音符一次删，速度事件按 8 条 TIL 一组删
        with m.open_edit(raw_notes=True, event_scan_til_ids=[0]) as tx:
            if not tx.chart.bpms or abs(tx.chart.bpms[0].bpm - BPM) > 1e-6:
                raise SystemExit(f"当前谱面 BPM 不是 {BPM}，中止")
            n0 = len(tx.chart.notes)
            def old_end_note(n) -> bool:   # 上一次写入的结尾判定音符
                i = n.info
                return (int(i.til) == 0 and i.x == 0 and i.w == 16 and i.h == args.end_h
                        and int(i.option_value) == GRID and lo <= int(i.t) <= hi)
            tx.chart.notes = [n for n in tx.chart.notes
                              if not (int(n.info.til) in tils and lo <= int(n.info.t) <= hi)
                              and not (args.end_note and old_end_note(n))]
            dn = n0 - len(tx.chart.notes)
        de = 0
        for k in range(0, len(tils), 8):
            chunk = tils[k:k + 8]
            with m.open_edit(raw_notes=True, event_scan_til_ids=chunk,
                             event_scan_lookahead_ticks=lookahead) as tx:
                e0 = len(tx.chart.tils)
                tx.chart.tils = [e for e in tx.chart.tils
                                 if not (e.til in chunk and lo <= e.t <= hi)]
                de += e0 - len(tx.chart.tils)
        print(f"清除 TIL {tils[0]}..{tils[-1]} 在 tick {lo}..{hi} 的旧内容：音符 {dn}，速度事件 {de}")

        allev = [TimelineSpeedEvent(til, t, float(sp))
                 for til, ev in events.items() for t, sp in sorted(ev.items())]
        for k in range(0, len(allev), 100000):     # 分批，避免单条消息超过 16MB
            with m.open_edit(snapshot=False) as tx:
                tx.chart.tils.extend(allev[k:k + 100000])
            print(f"  速度事件 {min(k + 100000, len(allev))}/{len(allev)}")
        print(f"速度事件 {len(allev)} 个已写入")

        k = 0
        while k < len(lines):   # 每批最多 --batch 个音符，且控制点不超过 2*batch（RPC 单条消息上限 16MB）
            t0, npts, k1 = time.time(), 0, k
            while k1 < len(lines) and k1 - k < args.batch and npts + len(lines[k1][2]) <= 2 * args.batch:
                npts += len(lines[k1][2])
                k1 += 1
            k1 = max(k1, k + 1)
            with m.open_edit(snapshot=False, raw_notes=True) as tx:
                for color, til, cp in lines[k:k1]:
                    (t, x, w, h), *rest = cp
                    root = R.air_crush_begin(t=t, x=x, w=w, h=h, gap=0, color=color, til=til)
                    for j, (t, x, w, h) in enumerate(rest):
                        mk = R.air_crush_end if j == len(rest) - 1 else R.air_crush_control
                        root.children.append(mk(t=t, x=x, w=w, h=h, til=til))
                    tx.chart.notes.append(root)
            k = k1
            print(f"  已写 {k}/{len(lines)} 个 AirCrush（{time.time() - t0:.1f}s）")

        # 结尾判定音符：视频最后一帧放一条带判定的 AirCrush（TIL 0，h 远高于可见上限），
        # 否则 UMIGURI 会在最后一个判定音符处提前结束游戏画面。
        if args.end_note:
            t_end = args.end_tick
            with m.open_edit(snapshot=False, raw_notes=True) as tx:
                if getattr(args, "end_type", "aircrush") == "damage":
                    tx.chart.notes.append(R.damage(t=t_end, x=0, w=16, til=0))
                else:
                    r = R.air_crush_begin(t=t_end, x=0, w=16, h=args.end_h, gap=GRID, color=0, til=0)
                    r.children.append(R.air_crush_end(t=t_end + GRID, x=0, w=16, h=args.end_h, til=0))
                    tx.chart.notes.append(r)
            if getattr(args, "end_type", "aircrush") == "damage":
                print(f"结尾音符：TIL0 Damage t={t_end} x=0 w=16")
            else:
                print(f"结尾判定音符：TIL0 AirCrush t={t_end}->{t_end + GRID} x=0 w=16 h={args.end_h} gap={GRID}")
    print("完成。撤销用 m.undo()（每次提交一步），不要用界面 Ctrl+Z。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=0, help="谱面起始 tick（5 的倍数）")
    ap.add_argument("--anchor", type=int, default=0, help="视频取帧的 0 点 tick（默认 0 = 与音乐同步）")
    ap.add_argument("--video", default=None, help="视频文件（默认 badapple.mp4 或环境变量 BADAPPLE_VIDEO）")
    ap.add_argument("--audio-lag", type=float, default=None, help="谱面音频比视频晚多少秒（默认 0.2629，Bad Apple 实测）")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--rows", type=int, default=25)
    ap.add_argument("--htop", type=int, default=240)
    ap.add_argument("--jump", type=float, default=1000.0)
    ap.add_argument("--dot", type=float, default=1.0, help="点段 5 tick 的速度（点沿轨道方向的长度）")
    ap.add_argument("--tils", type=int, nargs="+", default=[1, 2], help="轮流使用的时间轴")
    ap.add_argument("--clear-tils", type=int, nargs="*", default=[], help="额外清除的时间轴")
    ap.add_argument("--colors", choices=["both", "white", "black"], default="both")
    ap.add_argument("--merge", choices=["none", "cols", "rows"], default="none")
    ap.add_argument("--store-gap", type=int, default=13440,
                    help="视频结束到存放区的间隔 tick。默认 13440（7 小节，约 12.2 s）：UMIGURI 在最后一个音符结束约 10 s 后停止画面，存放区放在这之后就看不到")
    ap.add_argument("--far", type=float, default=5e6, help="pertil/park 模式：画面不显示时所在的距离")
    ap.add_argument("--spacing", type=float, default=50000.0, help="park 模式：同一 TIL 相邻画面的间距")
    ap.add_argument("--til-base", type=int, default=100, help="pertil 模式：第 0 幅画面的 TIL 编号")
    ap.add_argument("--mode", choices=["overlap", "behind", "emerge", "store", "pertil", "park", "zero", "link"], default="link",
                    help="overlap：旧画面在判定线上被吃掉（5 tick 叠影）；"
                         "behind：旧画面瞬间退到判定线后方再被吃掉，新画面从远处飞来；"
                         "emerge：同 behind，且新画面藏在判定线后方，用负速度推出；"
                         "store：画面全部存放在视频之后，视频期间不吃掉任何画面；"
                         "pertil：每幅画面独占一条 TIL（需要上千条 TIL，Margrete 只支持 0-63，勿用）；"
                         "park：TIL 1-63 轮流，不显示时停在远处，切换全部高速掠过；"
                         "zero：同 park，但画面放在 0x 里，同色竖线用欧拉路径串成少量 AirCrush")
    ap.add_argument("--link", choices=["all", "top"], default="top",
                    help="link 模式：all = 每条 TIL 每种颜色一个 AirCrush（被包围的色块经判定线后方进出）；"
                         "top = 只经顶边串联，被包围的色块各自单独一个 AirCrush")
    ap.add_argument("--gray", action="store_true",
                    help="link 模式：三档灰度。每格两层，白白 = 白，白黑 = 灰，黑黑 = 黑（取代 --double 的两层同色）")
    ap.add_argument("--stub-at", choices=["top", "bottom"], default="top",
                    help="link all：被包围的色块从哪里经判定线后方进出。top = 色块最上沿最高的一列（残线落在色块自己身上）；"
                         "bottom = 最下沿（旧做法，黑白交界处有可见残线）")
    ap.add_argument("--behind", type=float, default=2000.0, help="link 模式：判定线后方移动时的距离 B")
    ap.add_argument("--sky", type=int, default=5000, help="link 模式：屏幕外移动时的高度 H")
    ap.add_argument("--end-type", choices=["damage", "aircrush"], default="damage",
                    help="视频最后一帧放的音符：damage（默认）或带判定的 AirCrush（旧规则）")
    ap.add_argument("--double", action="store_true", help="zero 模式：每条线来回各画一遍（AirCrush 更少，线条两层）")
    ap.add_argument("--remove", type=float, default=0.0, help="behind 模式退场速度（默认 jump/2）")
    ap.add_argument("--chain", type=int, default=0,
                    help="park 模式：把一帧里同色的线串成 AirCrush，每个最多 N 条线（0 = 不串，每条线一个音符）。"
                         "UMIGURI 的开销主要按音符个数算")
    ap.add_argument("--aspect", choices=["crop", "stretch"], default="crop",
                    help="crop：--htop 高于 220 时左右裁掉一些，保持比例（推荐配 --htop 240 --rows 25）；stretch：直接缩放")
    ap.add_argument("--threshold", type=int, default=128)
    ap.add_argument("--batch", type=int, default=20000, help="每次提交的音符数")
    ap.add_argument("--instance", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--no-end-note", dest="end_note", action="store_false",
                    help="不在最后一帧放结尾判定音符（默认会放，否则 UMIGURI 会提前结束画面）")
    ap.add_argument("--end-h", type=int, default=1000, help="结尾判定音符的高度（远高于可见上限 220）")
    args = ap.parse_args()
    global VIDEO, AUDIO_LAG
    if args.video:
        VIDEO = Path(args.video)
    if args.audio_lag is not None:
        AUDIO_LAG = args.audio_lag
    if not VIDEO.exists():
        raise SystemExit(f"找不到视频 {VIDEO}，用 --video 指定")
    if args.start % GRID:
        raise SystemExit("--start 必须是 5 的倍数")
    if args.chain and args.mode != "park":
        raise SystemExit("--chain 只用于 park 模式")
    if args.mode in ("park", "zero", "link") and args.tils == [1, 2]:
        args.tils = list(range(1, 64))
    if any(not 0 < t < 64 for t in args.tils):
        raise SystemExit("TIL 编号必须在 1..63（Margrete 只支持 0-63，0 留给正常谱面）")
    if args.mode != "pertil" and len(set(args.tils)) < 2:
        raise SystemExit("--tils 至少需要 2 条不同的时间轴（相邻帧必须在不同 TIL 上）")

    sched, idxs, pics, lines, events = build(args)
    lines = as_chains(lines)
    validate(lines, events, sched)
    n = len(sched) - 1
    nw = sum(l[0] == WHITE for l in lines)
    nseg = sum((len(cp) + 1) // 2 for _, _, cp in lines)
    print(f"帧数 {n}（{args.fps:g} fps），帧间隔 {sorted({b - a for a, b in zip(sched, sched[1:])})} tick，"
          f"模式 {args.mode}，tick {sched[0]}..{sched[-1]}，视频帧 {idxs[0]}..{idxs[-1]}，TIL {args.tils if len(args.tils) <= 4 else f'{args.tils[0]}..{args.tils[-1]}（{len(args.tils)} 条）'}")
    print(f"点阵 {COLS}x{args.rows}，h 0..{args.htop}，合并方式 {args.merge}，颜色 {args.colors}")
    print(f"AirCrush {len(lines)} 个（白 {nw} / 黑 {len(lines) - nw}），平均 {len(lines) / n:.1f} 个/帧；"
          f"可见线段 {nseg} 条，控制点 {sum(len(cp) for _, _, cp in lines)} 个")
    print(f"速度事件 {sum(map(len, events.values()))} 个：到场 {args.jump:g}x / 定格 0x / 点段 {args.dot:g}x")
    print("校验通过：全部 tick 为 5 的倍数，x/w 在轨道内，每条线严格递增")

    out = Path(__file__).with_name("badapple_preview.png")
    preview_png(pics, args.rows, out)
    if args.write:
        lo = min(min(ev) for ev in events.values())
        hi = max(max(ev) for ev in events.values()) + GRID
        args.end_tick = sched[-2]          # 最后一帧的显示起点
        write(args, lines, events, lo, hi)
    return 0


if __name__ == "__main__":
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
