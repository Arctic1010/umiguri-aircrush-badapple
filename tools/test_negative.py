"""诊断：UMIGURI 是否接受负速度？判定线后方（玩家这一侧）的未来音符是否会被画出来？

TIL 3 上放一幅静止测试图（31 条白色竖线 h 0..220 + 一条黑色横线 h 110），点段在第 38 小节开头。
用 5 tick 的速度脉冲（全部在 1/384 网格上）逐小节改变画面深度 D：

    UI 小节   D（tick 等效，正 = 判定线前方，负 = 判定线后方）
    31        0        参照：正常显示
    32        -20      刚退到判定线后面一点
    33        -200
    34        -2000
    35        -20000
    36        0        （负速度拉回）参照：应再次正常显示
    37        +1000    判定线前方 1000（顺便看可视距离）
    38 开头   0        点段，画面被吃掉

用法：.venv\\Scripts\\python.exe test_negative.py 0514
"""

from __future__ import annotations

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))   # 仓库根目录
import sys

from margrete_rpc import Margrete
from margrete_rpc.chart.events import TimelineSpeedEvent
from margrete_rpc.chart.notes import R

TIL = 3
BAR = 1920
GRID = 5
FIRST = 30 * BAR            # UI 第 31 小节
DEPTHS = [0, -20, -200, -2000, -20000, 0, 1000]
WHITE, BLACK = 10, 11


def main() -> int:
    inst = sys.argv[1] if len(sys.argv) > 1 else None
    p = FIRST + len(DEPTHS) * BAR   # 点段起点（UI 第 38 小节）
    # 以点段起点 X(P) = 0 为基准，当前 X = -D
    ev = {0: 0.0}
    x = 0.0
    for k, d in enumerate(DEPTHS):
        t = FIRST + k * BAR
        target = -d
        if target != x:
            ev[t] = (target - x) / GRID
            ev[t + GRID] = 0.0
            x = target
    if x != 0:
        ev[p - GRID] = (0 - x) / GRID
    ev[p] = 1.0
    ev[p + GRID] = 0.0
    assert all(t % GRID == 0 for t in ev)

    lines = []
    for j in range(31):
        x_, w_ = (j // 2, 1) if j % 2 == 0 else (j // 2, 2)   # c = 0.5 + 0.5j
        lines.append(((x_, w_, 220), (x_, w_, 0), WHITE))
    lines.append(((0, 1, 110), (15, 1, 110), BLACK))

    with Margrete(inst) as m:
        with m.open_edit(raw_notes=True, event_scan_til_ids=[0, TIL]) as tx:
            tx.chart.notes = [n for n in tx.chart.notes if int(n.info.til) != TIL]
            tx.chart.tils = [e for e in tx.chart.tils if e.til != TIL]
        with m.open_edit(snapshot=False, raw_notes=True) as tx:
            for t, sp in sorted(ev.items()):
                tx.chart.tils.append(TimelineSpeedEvent(TIL, t, sp))
            for (x0, w0, h0), (x1, w1, h1), col in lines:
                root = R.air_crush_begin(t=p, x=x0, w=w0, h=h0, gap=0, color=col, til=TIL)
                root.children.append(R.air_crush_end(t=p + GRID, x=x1, w=w1, h=h1, til=TIL))
                tx.chart.notes.append(root)
        with m.open_edit(raw_notes=True, event_scan_til_ids=[TIL]) as tx:
            got = sorted((e.t, e.speed) for e in tx.chart.tils if e.til == TIL)
            print("读回 TIL3 速度事件：", got)
            print("负速度被保留：", any(s < 0 for _, s in got))
    print(f"测试图 {len(lines)} 条，点段 tick {p}；事件 {sorted(ev.items())}")
    return 0


if __name__ == "__main__":
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
