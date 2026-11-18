"""诊断：0x 段（沿轨道方向长度为 0）里的线，哪些方向看得见？以及“串链 + 0x 连接段”是否干净。

六个变体依次在判定线上静止显示 2 小节（各用一条 TIL），中间隔 1 小节空白：
  第 2-3 小节   V1 方框（左右竖边、上下横边、一条斜线），1x 点段（深度 0->5）  —— 参照，应全部可见
  第 5-6 小节   V2 同一方框，全部在 0x 里（深度 0->0），每条边一个 AirCrush
  第 8-9 小节   V3 同一方框，点段速度 0.01x（深度 0->0.05）
  第 11-12 小节 V4 同一方框，0x，整个方框串成一个 AirCrush
  第 14-15 小节 V5 Bad Apple 一帧，按 --chain 方案串链（+1x/-1x 线段，0x 连接段）
  第 17-18 小节 V6 同一帧，每条线一个 AirCrush（现在的 park 方式）—— V5 的参照
画面存放在第 22 小节之后。最后一个变体结束处放一条结尾判定音符（TIL 0，h=1000）。

    python test_zero.py ID
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

G = 5
BAR = 1920
FAR = 5e6
Z = 22 * BAR                      # 存放区起点（第 22 小节）
VIDEO_FRAME = 1500                # V5/V6 用的视频帧（50 s 处）


def xw(c: float):
    return B.X_W[round(2 * c - 1)]


SQ = [((4, 180), (4, 40)), ((12, 180), (12, 40)),      # 左、右竖边
      ((4, 180), (12, 180)), ((4, 40), (12, 40)),      # 上、下横边
      ((4, 40), (12, 180))]                            # 斜线


def build():
    notes, knots = [], {}
    for v in range(1, 7):
        a = BAR * (2 + 3 * (v - 1))
        b = a + 2 * BAR
        kn = [(0, -FAR), (a - G, -FAR), (a, 0.0), (b, 0.0), (b + G, -FAR), (Z, -FAR)]
        q = Z + G
        if v in (1, 2, 3):
            dot = {1: 1.0, 2: 0.0, 3: 0.01}[v]
            for k, ((c0, h0), (c1, h1)) in enumerate(SQ):
                t = q + 2 * G * k
                notes.append((10, v, [(t, *xw(c0), h0), (t + G, *xw(c1), h1)]))
                kn += [(t, 0.0), (t + G, G * dot)]
            kn.append((q + 2 * G * len(SQ), 0.0))
        elif v == 4:
            path = [(4, 180), (4, 40), (12, 40), (12, 180), (4, 180), (12, 40)]
            notes.append((10, v, [(q + G * k, *xw(c), h) for k, (c, h) in enumerate(path)]))
            kn.append((q, 0.0))
        else:
            img = B.load_frames(VIDEO_FRAME, VIDEO_FRAME, 23)[VIDEO_FRAME]
            hs = [round(220 * (22 - r) / 22) for r in range(23)]
            ns = argparse.Namespace(threshold=128, colors="both", merge="cols")
            fl = B.frame_lines(img, 0, v, hs, ns)
            if v == 5:
                n = max(sum(l[3] == c for l in fl) for c in (B.WHITE, B.BLACK))
                for c in (B.WHITE, B.BLACK):
                    pts = []
                    for k, (_, p0, p1, _, _) in enumerate(l for l in fl if l[3] == c):
                        t = q + 2 * G * k
                        pts += [(t, *p0), (t + G, *p1)] if k % 2 == 0 else [(t, *p1), (t + G, *p0)]
                    notes.append((c, v, pts))
                for k in range(n):
                    t = q + 2 * G * k
                    kn += [(t, G * 1.0 if k % 2 else 0.0), (t + G, 0.0 if k % 2 else G * 1.0)]
                kn.append((q + 2 * G * n, 0.0))
            else:
                for _, p0, p1, c, _ in fl:
                    notes.append((c, v, [(q, *p0), (q + G, *p1)]))
                kn += [(q, 0.0), (q + G, G * 1.0), (q + 2 * G, 0.0)]
        knots[v] = kn
    end_t = BAR * (2 + 3 * 5) + 2 * BAR - G
    return notes, {v: B.knots_to_events(kn) for v, kn in knots.items()}, end_t


def main() -> int:
    inst = sys.argv[1]
    notes, events, end_t = build()
    B.validate(notes, events, [0])
    with Margrete(inst, timeout=900) as m:
        with m.open_edit(raw_notes=True, event_scan_til_ids=[0]) as tx:
            if not tx.chart.bpms or abs(tx.chart.bpms[0].bpm - 138) > 1e-6:
                raise SystemExit("目标谱面 BPM 不是 138，中止")
            if tx.chart.notes:
                raise SystemExit(f"目标谱面不是空的（{len(tx.chart.notes)} 条），中止")
        with m.open_edit(snapshot=False, raw_notes=True) as tx:
            for v, ev in events.items():
                tx.chart.tils.extend(TimelineSpeedEvent(v, t, float(s)) for t, s in sorted(ev.items()))
            for color, til, cp in notes:
                (t, x, w, h), *rest = cp
                r = R.air_crush_begin(t=t, x=x, w=w, h=h, gap=0, color=color, til=til)
                for j, (t, x, w, h) in enumerate(rest):
                    mk = R.air_crush_end if j == len(rest) - 1 else R.air_crush_control
                    r.children.append(mk(t=t, x=x, w=w, h=h, til=til))
                tx.chart.notes.append(r)
            r = R.air_crush_begin(t=end_t, x=0, w=16, h=1000, gap=G, color=0, til=0)
            r.children.append(R.air_crush_end(t=end_t + G, x=0, w=16, h=1000, til=0))
            tx.chart.notes.append(r)
    print(f"已写入：AirCrush {len(notes)} 个，速度事件 {sum(map(len, events.values()))} 个，结尾判定音符 t={end_t}")
    print("V5/V6 每个 AirCrush 的控制点数：", [len(cp) for c, til, cp in notes if til == 5])
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
