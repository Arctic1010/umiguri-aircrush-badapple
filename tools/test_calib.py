"""标定：判定线上（深度 0）屏幕上沿对应的 h。

两屏静止画面，每屏 16 条白色竖线，第 k 道（从左数第 k+1 道，k = 0..15）一条，从 h=150 画到 h=顶。
  第 3-5 小节   细标定：第 k 道的顶 = 220 + 20k   （220, 240, …, 520）
  第 7-9 小节   粗标定：第 k 道的顶 = 220 + 60k   （220, 280, …, 1120）
请看哪些道的线被屏幕上沿截断（顶部看不到），报告第一条被截断的是第几道。
最后放一个 Damage 音符；存放区在结尾 12 s 以后。
    python test_calib.py ID     （会先清空谱面上全部音符和 TIL 1-2 的速度事件）
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))   # 仓库根目录
import sys

import badapple_air as B
from margrete_rpc import Margrete
from margrete_rpc.chart.events import TimelineSpeedEvent
from margrete_rpc.chart.notes import R

sys.stdout.reconfigure(encoding="utf-8")
G, BAR, FAR = 5, 1920, 5e6
SCREENS = [(1, 2 * BAR, 220, 20), (2, 6 * BAR, 220, 60)]     # (TIL, 显示起点, 第 0 道顶, 步长)
SHOW = 3 * BAR
END = 6 * BAR + SHOW - G
Z = END + 13440


def main() -> int:
    inst = sys.argv[1]
    with Margrete(inst, timeout=900) as m:
        tils = list(range(64))
        for k in range(0, 64, 8):
            with m.open_edit(raw_notes=True, event_scan_til_ids=tils[k:k + 8],
                             event_scan_lookahead_ticks=200000) as tx:
                if k == 0:
                    if abs(tx.chart.bpms[0].bpm - 138) > 1e-6:
                        raise SystemExit("BPM 不是 138")
                    print(f"清除音符 {len(tx.chart.notes)}")
                    tx.chart.notes = []
                tx.chart.tils = [e for e in tx.chart.tils if e.til == 0]
        with m.open_edit(snapshot=False, raw_notes=True) as tx:
            for i, (til, a, top0, step) in enumerate(SCREENS):
                q = Z + 1000 * i
                kn = [(0, -FAR), (a - G, -FAR), (a, 0.0), (a + SHOW, 0.0), (a + SHOW + G, -FAR), (Z - G, -FAR), (q, 0.0)]
                for k in range(16):
                    t = q + 2 * G * k
                    x, w = k, 1
                    r = R.air_crush_begin(t=t, x=x, w=w, h=150, gap=0, color=10, til=til)
                    r.children.append(R.air_crush_end(t=t + G, x=x, w=w, h=top0 + step * k, til=til))
                    tx.chart.notes.append(r)
                ev = B.knots_to_events(kn)
                tx.chart.tils.extend(TimelineSpeedEvent(til, t, float(s)) for t, s in sorted(ev.items()))
            tx.chart.notes.append(R.damage(t=END, x=0, w=16, til=0))
    print("已写入两屏标定：第 3-5 小节 顶 = 220+20k；第 7-9 小节 顶 = 220+60k（k = 第几道 − 1）。结尾 Damage 音符 t =", END)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
