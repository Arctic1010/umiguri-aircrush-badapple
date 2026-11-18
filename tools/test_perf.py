"""UMIGURI 性能对照测试：完整版只有约 3 fps，判断瓶颈是“速度事件数”还是“物件数”。

在一张新的空谱面（BPM 138）上依次运行，每步之后到 UMIGURI 里看前 5 秒的帧率：
    python test_perf.py base   ID   # 1) 前 5 秒视频（park），存放区放到曲尾 tick 258465（和完整版一样远）
    python test_perf.py events ID   # 2) 再加约 4.2 万个空速度事件（speed 0 -> 0，不改变画面），插在视频与存放区之间
    python test_perf.py unevents ID # 2') 删掉这些空速度事件，回到 1)
    python test_perf.py notes  ID   # 3) 再加约 44.8 万条永远在 1e8 远处的空 AirCrush（TIL 1，tick 262000 起）
    python test_perf.py chains ID   # 4) 清空后重写 1)，再加同样多的空线段，但每 32 条打包成一个 AirCrush
空事件 / 空物件都放在完整版对应的位置附近，数量与完整版相同。
"""

from __future__ import annotations

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))   # 仓库根目录
import argparse
import sys

import badapple_air as B
from margrete_rpc import Margrete
from margrete_rpc.chart.events import TimelineSpeedEvent
from margrete_rpc.chart.notes import R

GRID = 5
STORE_Z = 258465          # 完整版存放区起点
DUMMY_T = 262000          # 空物件起点（在存放区之后）
FULL_EVENTS = 42013
FULL_NOTES = 454123


def build_base():
    a = argparse.Namespace(chain=0, 
        start=0, anchor=0, seconds=5.0, fps=30.0, rows=23, htop=220, jump=1000.0, dot=1.0,
        tils=list(range(1, 64)), clear_tils=[], colors="both", merge="cols", threshold=128,
        batch=20000, instance=None, write=False, far=5e6, til_base=100, mode="park",
        remove=0.0, spacing=50000.0, store_gap=0,
    )
    sched = B.frame_schedule(0, a.seconds, a.fps)
    a.store_gap = STORE_Z - (sched[-1] + 2 * GRID)
    return a, B.build(a)


def main() -> int:
    cmd, inst = sys.argv[1], sys.argv[2]
    a, (sched, idxs, pics, lines, events) = build_base()
    with Margrete(inst, timeout=900) as m:
        if cmd != "chains":   # chains：谱面已超 16MB，读不了快照，跳过检查
          with m.open_edit(raw_notes=True, event_scan_til_ids=[0]) as tx:
            if not tx.chart.bpms or abs(tx.chart.bpms[0].bpm - 138) > 1e-6:
                raise SystemExit("目标谱面 BPM 不是 138，中止")
            if cmd == "base" and tx.chart.notes:
                raise SystemExit(f"目标谱面不是空的（{len(tx.chart.notes)} 条），中止")

        if cmd == "base":
            a.instance, a.write = inst, True
            a.end_note, a.end_h, a.end_tick = True, 1000, sched[-2]
            B.write(a, lines, events, 0, max(max(ev) for ev in events.values()) + GRID)

        elif cmd in ("events", "unevents"):
            per_til = (FULL_EVENTS - sum(map(len, events.values()))) // 63
            dummy = {}
            for til, ev in events.items():
                t0 = max(t for t in ev if t < STORE_Z - GRID) + GRID   # 停放段开始（speed 0）
                assert ev[max(t for t in ev if t < t0)] == 0.0
                step = max(GRID, ((STORE_Z - GRID - t0) // per_til) // GRID * GRID)
                dummy[til] = [t for t in range(t0 + step, STORE_Z - GRID, step) if t not in ev][:per_til]
            n = sum(map(len, dummy.values()))
            if cmd == "events":
                with m.open_edit(snapshot=False) as tx:
                    for til, ts in dummy.items():
                        tx.chart.tils.extend(TimelineSpeedEvent(til, t, 0.0) for t in ts)
                print(f"已加空速度事件 {n} 个（总事件约 {n + sum(map(len, events.values()))}）")
            else:
                removed = 0
                tils = sorted(dummy)
                for k in range(0, len(tils), 8):
                    ch = tils[k:k + 8]
                    keys = {(til, t) for til in ch for t in dummy[til]}
                    with m.open_edit(raw_notes=True, event_scan_til_ids=ch,
                                     event_scan_lookahead_ticks=STORE_Z + 20000) as tx:
                        n0 = len(tx.chart.tils)
                        tx.chart.tils = [e for e in tx.chart.tils if (e.til, int(e.t)) not in keys]
                        removed += n0 - len(tx.chart.tils)
                print(f"已删空速度事件 {removed} 个")

        elif cmd == "notes":
            n = FULL_NOTES - len(lines)
            ev1 = events[1]
            last = max(ev1)
            assert ev1[last] == 0.0 and last < DUMMY_T - GRID
            span = 1120
            with m.open_edit(snapshot=False) as tx:
                tx.chart.tils += [TimelineSpeedEvent(1, DUMMY_T - GRID, 2e7),       # X += 1e8
                                  TimelineSpeedEvent(1, DUMMY_T, 1.0),
                                  TimelineSpeedEvent(1, DUMMY_T + span + GRID, 0.0)]
            for k in range(0, n, 20000):
                with m.open_edit(snapshot=False, raw_notes=True) as tx:
                    for j in range(k, min(n, k + 20000)):
                        t = DUMMY_T + GRID * ((j // 31) % (span // GRID))
                        x, w = B.X_W[j % 31]
                        h = 10 * (j % 23)
                        r = R.air_crush_begin(t=t, x=x, w=w, h=h, gap=0, color=10 + j % 2, til=1)
                        r.children.append(R.air_crush_end(t=t + GRID, x=x, w=w, h=h, til=1))
                        tx.chart.notes.append(r)
                print(f"  空物件 {min(n, k + 20000)}/{n}")
        elif cmd == "chains":
            # 4) 同样 44.8 万条可见线段，但每 32 条打包成一个 AirCrush（中间用连接段串起来）
            with m.open_edit(snapshot=False, replace_all_notes=True) as tx:
                pass
            print("已清空全部音符")
            with m.open_edit(snapshot=False, raw_notes=True) as tx:
                for color, til, ((p, x0, w0, h0), (_, x1, w1, h1)) in lines:
                    r = R.air_crush_begin(t=p, x=x0, w=w0, h=h0, gap=0, color=color, til=til)
                    r.children.append(R.air_crush_end(t=p + GRID, x=x1, w=w1, h=h1, til=til))
                    tx.chart.notes.append(r)
                t_end = sched[-2]
                r = R.air_crush_begin(t=t_end, x=0, w=16, h=1000, gap=GRID, color=0, til=0)
                r.children.append(R.air_crush_end(t=t_end + GRID, x=0, w=16, h=1000, til=0))
                tx.chart.notes.append(r)
            print(f"已重写前 5 秒视频 {len(lines)} 条 + 结尾判定音符")
            n, per = FULL_NOTES - len(lines), 32
            nch = -(-n // per)
            starts = (1120 - 10 * per) // GRID
            for k in range(0, nch, 2000):
                with m.open_edit(snapshot=False, raw_notes=True) as tx:
                    for c in range(k, min(nch, k + 2000)):
                        t = DUMMY_T + GRID * (c % starts)
                        cnt = min(per, n - c * per)
                        root = None
                        for q in range(cnt):
                            x, w = B.X_W[(c + q) % 31]
                            h0, h1 = 10 * (q % 23), 10 * ((q + 5) % 23)
                            if root is None:
                                root = R.air_crush_begin(t=t, x=x, w=w, h=h0, gap=0,
                                                         color=10 + c % 2, til=1)
                            else:
                                root.children.append(R.air_crush_control(t=t, x=x, w=w, h=h0, til=1))
                            mk = R.air_crush_end if q == cnt - 1 else R.air_crush_control
                            root.children.append(mk(t=t + GRID, x=x, w=w, h=h1, til=1))
                            t += 2 * GRID
                        tx.chart.notes.append(root)
                print(f"  链 {min(nch, k + 2000)}/{nch}")
            print(f"空线段 {n} 条打包成 {nch} 个 AirCrush")
        else:
            raise SystemExit("命令：base / events / unevents / notes / chains")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
