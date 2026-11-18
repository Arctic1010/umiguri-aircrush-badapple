"""校验 link 模式：按速度事件算每个控制点的 X，把每段分类，只允许：
   内容竖线（0x，h<=220）/ 横跳（h 不变，0x）/ 屏幕外（h>=220 的竖线或两端都在 H）/ 后方（两端都在 X=mΔ-B）/ 残线（同点，X 差 B）。
   再逐帧检查内容竖线 = 原画面的格子（每条两遍）。
   python check_link.py 秒数 all|top"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))   # 仓库根目录
import sys, argparse, bisect
from collections import Counter, defaultdict
import badapple_air as B
from euler_paths import column_runs, edges_h
sys.stdout.reconfigure(encoding="utf-8")
secs, link = float(sys.argv[1]), sys.argv[2]
a = argparse.Namespace(start=0, anchor=0, seconds=secs, fps=float(sys.argv[6]) if len(sys.argv) > 6 else 30.0, rows=int(sys.argv[3]), htop=int(sys.argv[4]), jump=1000.0, dot=1.0,
    tils=list(range(1, 64)), clear_tils=[], colors="both", merge="cols", threshold=128, batch=20000,
    far=5e6, til_base=100, mode="link", remove=0.0, spacing=50000.0, store_gap=13440, chain=0, double=True, aspect="crop",
    link=link, behind=2000.0, sky=5000, stub_at=(sys.argv[5] if len(sys.argv) > 5 else "top"),
    gray=len(sys.argv) > 7 and sys.argv[7] == "gray")
sched, idxs, pics, lines, events = B.build(a)
B.validate(lines, events, sched)
D, BH, H, TOPH = a.spacing, a.behind, a.sky, a.htop
X = {}
for til, ev in events.items():
    ts = sorted(ev); xs = [0.0 if til == a.tils[0] else -a.far]   # 第 0 帧所在的 TIL 从 X=0 开始
    for t0, t1 in zip(ts, ts[1:]): xs.append(xs[-1] + ev[t0] * (t1 - t0))
    X[til] = (ts, xs, ev)
def x_at(til, t):
    ts, xs, ev = X[til]; i = bisect.bisect_right(ts, t) - 1
    return xs[i] + ev[ts[i]] * (t - ts[i])
kinds = Counter(); bad = []
content = defaultdict(Counter)       # (til, m) -> Counter
for c, til, pts in lines:
    P = [(t, x, w, h, x_at(til, t)) for t, x, w, h in pts]
    for (t0, x0, w0, h0, X0), (t1, x1, w1, h1, X1) in zip(P, P[1:]):
        m0, r0 = divmod(X0, D); m1, r1 = divmod(X1, D)
        if X0 == X1 and r0 == 0 and (x0, w0) == (x1, w1) and h0 != h1 and max(h0, h1) <= TOPH:
            kinds["内容竖线"] += 1; content[(til, int(m0))][(c, x0, w0, min(h0, h1), max(h0, h1))] += 1
        elif X0 == X1 and h0 == h1 and h0 <= TOPH:
            kinds["横跳"] += 1
        elif h0 == h1 == H:
            kinds["屏幕外(H)"] += 1
        elif X0 == X1 and (x0, w0) == (x1, w1) and min(h0, h1) >= TOPH:
            kinds["屏幕外竖线"] += 1
        elif X0 == X1 and (X0 + BH) % D == 0:
            kinds["后方"] += 1
        elif (x0, w0, h0) == (x1, w1, h1) and abs(X0 - X1) == BH and (r0 == 0 or r1 == 0):
            kinds["残线"] += 1
        else:
            bad.append((c, til, t0, (x0, w0, h0, X0), (x1, w1, h1, X1)))
E = edges_h(a.rows, a.htop)
if a.gray:
    from zero_link import gray_levels
    lvl = {h: k for k, h in enumerate(E)}
    mism = changed = 0
    for i, img in enumerate(pics):
        til, m = a.tils[i % 63], i // 63
        w = gray_levels(img, a.rows, B.COLS)
        raw = [[0 if img[r * B.COLS + j] < 85 else 1 if img[r * B.COLS + j] < 170 else 2 for j in range(B.COLS)] for r in range(a.rows)]
        changed += sum(w[r][j] != raw[r][j] for r in range(a.rows) for j in range(B.COLS))
        assert all(sum(x == 1 for x in row) % 2 == 0 for row in w)
        cnt = defaultdict(Counter)            # (r, j) -> {color: n}
        for (c, x, wd, h0, h1), n in content[(til, m)].items():
            j = B.X_W.index((x, wd))
            for k in range(lvl[h0], lvl[h1]):
                cnt[(a.rows - 1 - k, j)][c] += n
        ok = all(cnt[(r, j)][B.WHITE] == w[r][j] and cnt[(r, j)][B.BLACK] == 2 - w[r][j]
                 for r in range(a.rows) for j in range(B.COLS)) and len(cnt) == a.rows * B.COLS
        mism += not ok
    n = len(sched) - 1
    print(f"link={link} gray {secs}s：AirCrush {len(lines)} 个，控制点 {sum(len(p) for _,_,p in lines)}，"
          f"速度事件 {sum(map(len, events.values()))}，存放区 tick {min(p[0][0] for _,_,p in lines)}..{max(p[-1][0] for _,_,p in lines)}")
    print("  分段：", dict(kinds), " 不合规：", len(bad), bad[:3])
    print(f"  逐格校验（每格两层，白层数 = 灰度档）：{n} 帧中不一致 {mism} 帧；为凑偶数改动的格子 {changed} 个"
          f"（占 {changed / (n * a.rows * B.COLS):.2%}）")
    sys.exit(0)
mism = 0
for i, img in enumerate(pics):
    til, m = a.tils[i % 63], i // 63
    grid = [[B.WHITE if img[r * B.COLS + j] >= 128 else B.BLACK for j in range(B.COLS)] for r in range(a.rows)]
    want = Counter()
    for c in (B.WHITE, B.BLACK):
        for j, lo, hi in column_runs(grid, a.rows, B.COLS, c):
            want[(c, *B.X_W[j], E[lo], E[hi])] += 2
    mism += want != content[(til, m)]
n = len(sched) - 1
print(f"link={link} {secs}s：AirCrush {len(lines)} 个，控制点 {sum(len(p) for _,_,p in lines)}，"
      f"速度事件 {sum(map(len, events.values()))}，存放区 tick {min(p[0][0] for _,_,p in lines)}..{max(p[-1][0] for _,_,p in lines)}")
print("  分段：", dict(kinds), " 不合规：", len(bad), bad[:3])
print(f"  覆盖校验：{n} 帧中不一致 {mism} 帧")
for i, img in enumerate(pics):
    til, m = a.tils[i % 63], i // 63
    grid = [[B.WHITE if img[r * B.COLS + j] >= 128 else B.BLACK for j in range(B.COLS)] for r in range(a.rows)]
    want = Counter()
    for c in (B.WHITE, B.BLACK):
        for j, lo, hi in column_runs(grid, a.rows, B.COLS, c):
            want[(c, *B.X_W[j], E[lo], E[hi])] += 2
    if want != content[(til, m)]:
        print("  帧", i, "til", til, "m", m, "缺", dict(want - content[(til, m)]), "多", dict(content[(til, m)] - want))
        break
