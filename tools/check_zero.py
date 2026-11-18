"""校验 zero 模式：每幅画面的控制点都在同一卷动量（0x），相邻控制点只有竖线或横线，没有斜线；覆盖与 park 相同的格子。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))   # 仓库根目录
import sys, argparse, bisect
from collections import Counter
import badapple_air as B
from euler_paths import column_runs, edges_h
sys.stdout.reconfigure(encoding="utf-8")
secs, double = float(sys.argv[1]), sys.argv[2] == "double"
a = argparse.Namespace(start=0, anchor=0, seconds=secs, fps=30.0, rows=23, htop=220, jump=1000.0, dot=1.0,
    tils=list(range(1, 64)), clear_tils=[], colors="both", merge="cols", threshold=128, batch=20000,
    far=5e6, til_base=100, mode="zero", remove=0.0, spacing=50000.0, store_gap=1920, chain=0, double=double)
sched, idxs, pics, lines, events = B.build(a)
B.validate(lines, events, sched)
X = {}
for til, ev in events.items():
    ts = sorted(ev); xs = [-5e6]
    for t0, t1 in zip(ts, ts[1:]): xs.append(xs[-1] + ev[t0] * (t1 - t0))
    X[til] = (ts, xs, ev)
def x_at(til, t):
    ts, xs, ev = X[til]; i = bisect.bisect_right(ts, t) - 1
    return xs[i] + ev[ts[i]] * (t - ts[i])
notflat = diag = 0; vert = Counter()
for c, til, pts in lines:
    xv = {x_at(til, t) for t, *_ in pts}
    if len(xv) != 1 or list(xv)[0] % 50000: notflat += 1
    for (t0, x0, w0, h0), (t1, x1, w1, h1) in zip(pts, pts[1:]):
        if (x0, w0) != (x1, w1) and h0 != h1: diag += 1
        if (x0, w0) == (x1, w1) and h0 != h1: vert[(c, til, x0, w0, min(h0, h1), max(h0, h1), t0 // 1)] += 0
# 覆盖校验：按帧重算竖线集合
E = edges_h(23, 220)
ok = True
by_pic = {}
for c, til, pts in lines:
    by_pic.setdefault((til, pts[0][0]), []).append((c, pts))
want = Counter(); got = Counter()
for (til, q), group in by_pic.items():
    pass
seg = sum(1 for c, til, pts in lines for p0, p1 in zip(pts, pts[1:]) if p0[1:3] == p1[1:3] and p0[3] != p1[3])
n = len(sched) - 1
print(f"zero{' double' if double else ''} {secs}s：AirCrush {len(lines)} 个（{len(lines)/n:.2f}/帧），竖线段 {seg}，"
      f"控制点 {sum(len(p) for _,_,p in lines)}，速度事件 {sum(map(len, events.values()))}，"
      f"存放区 tick {min(p[0][0] for _,_,p in lines)}..{max(p[-1][0] for _,_,p in lines)}")
print(f"  不在同一卷动量的 AirCrush: {notflat}，斜线段: {diag}")
pics_by_til = {}
for c, til, pts in lines:
    pics_by_til.setdefault(til, {}).setdefault(pts[0][0], []).append((c, pts))
bad = 0
for i, img in enumerate(pics):
    til, m = a.tils[i % 63], i // 63
    q = sorted(pics_by_til[til])[m]
    grid = [[B.WHITE if img[r * B.COLS + j] >= 128 else B.BLACK for j in range(B.COLS)] for r in range(23)]
    want = Counter()
    for c in (B.WHITE, B.BLACK):
        for j, lo, hi in column_runs(grid, 23, B.COLS, c):
            want[(c, *B.X_W[j], E[lo], E[hi])] += 2 if double else 1
    got = Counter()
    for c, pts in pics_by_til[til][q]:
        for p0, p1 in zip(pts, pts[1:]):
            if p0[3] != p1[3]:
                got[(c, p0[1], p0[2], min(p0[3], p1[3]), max(p0[3], p1[3]))] += 1
    bad += want != got
print(f"  覆盖校验：{len(pics)} 帧中不一致 {bad} 帧")
