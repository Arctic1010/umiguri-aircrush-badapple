"""AirCrush 链式绘图工具：把任意折线图案切成合法的 AirCrush 链并写入 Margrete。

几何模型（Margrete / UMIGURI 实际绘制规则）
    控制点 i -> 空间点 P_i = (t_i, c_i, h_i)，c_i = x_i + w_i / 2（note 宽度中点）
    相邻控制点之间连三维直线段；整条 AirCrush = 这些线段的折线。

硬约束（任何写入前都会校验，违反直接抛异常、不提交）
    1. 384 分割：全音符 1920 tick / 384 = 5 tick，所有 t 必须是 5 的倍数（且 >= 0）。
    2. 严格单调：同一条链内 t_0 < t_1 < ... < t_n。
    3. 点限制：轨道宽 16，w >= 1 的整数，0 <= x、x + w <= 16
       => c ∈ {0.5, 1.0, ..., 15.5}（步长 0.5）。
    4. 直线性质：曲线只能用密集控制点做折线拟合。

由于 2，图案中“时间方向回头”的地方必须断开成多条链；时间不变（水平）的线段
无法用任何链精确表示，按 flat 策略处理（默认斜移 1 格 = 5 tick）。

用法
    python aircrush_kit.py            # 演示：只计算 + 校验 + 打印链，不写入
    python aircrush_kit.py --write    # 演示图案写入到当前播放头位置（m.undo() 可撤销）

作为库：
    from aircrush_kit import polyline_to_chains, write_chains
    chains = polyline_to_chains([(0, 2, 0), (480, 14, 0), (960, 2, 0)])
    write_chains(chains, color="red")
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from margrete_rpc.chart.notes import AIRCRUSH_GAP_TRACELIKE, AirCrush

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TICKS_PER_WHOLE = 1920
MAX_DIVISION = 384
GRID = TICKS_PER_WHOLE // MAX_DIVISION  # 5 tick
FIELD_WIDTH = 16
C_MIN = Fraction(1, 2)                  # 0.5
C_MAX = Fraction(FIELD_WIDTH) - C_MIN   # 15.5

FlatPolicy = Literal["skew", "drop", "error"]


# ---------------------------------------------------------------------------
# 基础类型
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Pt:
    """链上的一个控制点。c 用 Fraction 保存，避免 0.5 步长的浮点误差。"""

    t: int
    c: Fraction
    h: int

    def __str__(self) -> str:
        return f"(t={self.t}, c={float(self.c):g}, h={self.h})"


Chain = list[Pt]


class ChainError(ValueError):
    """链违反了任一硬约束。"""


# ---------------------------------------------------------------------------
# 吸附 / 坐标换算
# ---------------------------------------------------------------------------
def snap_t(t: float) -> int:
    """时间吸附到 1/384 网格（5 tick）。"""
    return int(round(t / GRID)) * GRID


def snap_c(c: float) -> Fraction:
    """中点吸附到 0.5 步长并夹到 [0.5, 15.5]。"""
    s = Fraction(round(c * 2), 2)
    return min(max(s, C_MIN), C_MAX)


def snap(t: float, c: float, h: float) -> Pt:
    return Pt(snap_t(t), snap_c(c), max(0, int(round(h))))


def center_to_xw(c: Fraction) -> tuple[int, int]:
    """合法中点 -> (x, w)。半格用 w=1，整格用 w=2（最窄表示，且永不越界）。"""
    if c.denominator == 2:          # 0.5, 1.5, ...
        return int(c - Fraction(1, 2)), 1
    return int(c) - 1, 2            # 1, 2, ..., 15


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_point(p: Pt) -> None:
    if p.t < 0 or p.t % GRID:
        raise ChainError(f"{p}: t 不在 1/{MAX_DIVISION} 网格（需为 {GRID} 的非负倍数）")
    if (p.c * 2).denominator != 1 or not (C_MIN <= p.c <= C_MAX):
        raise ChainError(f"{p}: c 必须是 0.5 步长且在 [0.5, 15.5]")
    if p.h < 0:
        raise ChainError(f"{p}: h 不能为负")
    x, w = center_to_xw(p.c)
    if w < 1 or x < 0 or x + w > FIELD_WIDTH:  # 防御性检查
        raise ChainError(f"{p}: x={x} w={w} 越出轨道")


def validate_chain(chain: Sequence[Pt]) -> None:
    if len(chain) < 2:
        raise ChainError("一条 AirCrush 至少需要 2 个控制点（起点 + 1 个节点）")
    for p in chain:
        validate_point(p)
    for a, b in zip(chain, chain[1:]):
        if b.t <= a.t:
            raise ChainError(f"非严格单调: {a} -> {b}")


def validate_aircrush(note: AirCrush) -> None:
    """校验一个已存在的 AirCrush（读谱面时用）。"""
    pts = [(int(note.t), note.x, note.w)] + [(int(j.t), j.x, j.w) for j in note.joints]
    for t, x, w in pts:
        if t % GRID or w < 1 or x < 0 or x + w > FIELD_WIDTH:
            raise ChainError(f"AirCrush 点 t={t} x={x} w={w} 违规")
    ticks = [t for t, _, _ in pts]
    if any(b <= a for a, b in zip(ticks, ticks[1:])):
        raise ChainError(f"AirCrush t={ticks[0]} 非严格单调")


# ---------------------------------------------------------------------------
# 切链
# ---------------------------------------------------------------------------
def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


def _collinear(a: Pt, b: Pt, c: Pt) -> bool:
    """三维共线（精确有理数运算）：b 在 a->c 线段上，可删。"""
    dt1, dt2 = b.t - a.t, c.t - a.t
    return (b.c - a.c) * dt2 == (c.c - a.c) * dt1 and (b.h - a.h) * dt2 == (c.h - a.h) * dt1


def simplify(chain: Chain) -> Chain:
    """删除共线的中间点；折线形状完全不变，只减少控制点数。"""
    out: Chain = []
    for p in chain:
        while len(out) >= 2 and _collinear(out[-2], out[-1], p):
            out.pop()
        out.append(p)
    return out


def _flat_chain(a: Pt, b: Pt, policy: FlatPolicy) -> Chain | None:
    """处理 t 相同、c/h 不同的水平段。"""
    if policy == "drop":
        return None
    if policy == "error":
        raise ChainError(f"水平段无法表示: {a} -> {b}")
    # skew：终点后移 1 个网格（1/384），视觉上几乎水平
    return [a, Pt(b.t + GRID, b.c, b.h)]


def polyline_to_chains(
    points: Iterable[tuple[float, float, float]],
    *,
    flat: FlatPolicy = "skew",
    closed: bool = False,
) -> list[Chain]:
    """把一条按“绘制顺序”给出的折线 (t, c, h) 切成若干条合法链。

    - 先吸附到 5 tick / 0.5 中点网格，去掉连续重复点；
    - 按时间方向 (+/-) 分段，方向翻转处断开（转折点被相邻两条链共享）；
    - 时间倒退的段整体反转，使链内 t 严格递增；
    - t 不变的水平段按 ``flat`` 策略处理；
    - 最后删除共线点并逐条校验。
    """
    pts: list[Pt] = []
    for t, c, h in points:
        p = snap(t, c, h)
        if not pts or p != pts[-1]:
            pts.append(p)
    if closed and len(pts) > 2 and pts[0] != pts[-1]:
        pts.append(pts[0])

    chains: list[Chain] = []
    run: Chain = []
    run_dir = 0

    def flush() -> None:
        nonlocal run, run_dir
        if len(run) >= 2:
            chains.append(run if run_dir > 0 else run[::-1])
        run, run_dir = [], 0

    for a, b in zip(pts, pts[1:]):
        d = _sign(b.t - a.t)
        if d == 0:
            flush()
            fc = _flat_chain(a, b, flat)
            if fc:
                chains.append(fc)
            continue
        if d != run_dir:
            flush()
            run, run_dir = [a], d
        run.append(b)
    flush()

    chains = [simplify(ch) for ch in chains]
    for ch in chains:
        validate_chain(ch)
    return chains


def offset_chains(chains: Iterable[Chain], dt: int) -> list[Chain]:
    """整体平移时间（dt 必须在网格上）。"""
    if dt % GRID:
        raise ChainError(f"平移量 {dt} 不在 1/{MAX_DIVISION} 网格")
    return [[Pt(p.t + dt, p.c, p.h) for p in ch] for ch in chains]


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def chain_to_aircrush(chain: Chain, *, color: str = "default", gap: int = AIRCRUSH_GAP_TRACELIKE) -> AirCrush:
    validate_chain(chain)
    x0, w0 = center_to_xw(chain[0].c)
    note = AirCrush(t=chain[0].t, x=x0, w=w0, h=chain[0].h, gap=gap, color=color)
    for p in chain[1:]:
        x, w = center_to_xw(p.c)
        note.add_ctrl(t=p.t, x=x, w=w, h=p.h)
    return note


def write_chains(chains: Sequence[Chain], *, color: str = "default", gap: int = AIRCRUSH_GAP_TRACELIKE) -> int:
    """把链作为一次可撤销编辑写入 Margrete；返回写入条数。"""
    from margrete_rpc import Margrete

    notes = [chain_to_aircrush(ch, color=color, gap=gap) for ch in chains]  # 全部先校验
    with Margrete() as m, m.open_edit() as tx:
        tx.chart.notes.extend(notes)
    return len(notes)


def describe(chains: Sequence[Chain]) -> str:
    lines = [f"共 {len(chains)} 条链，{sum(len(c) for c in chains)} 个控制点"]
    for i, ch in enumerate(chains):
        lines.append(f"  链{i}: " + " -> ".join(str(p) for p in ch))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 演示
# ---------------------------------------------------------------------------
def _demo_shape() -> list[tuple[float, float, float]]:
    """一个闭合三角形 + 底边（水平段）：演示切链与 flat 策略。时间跨 1 小节。"""
    return [(0, 8, 0), (1920, 15.5, 0), (1920, 0.5, 0)]


def main() -> int:
    chains = polyline_to_chains(_demo_shape(), closed=True)
    print(describe(chains))

    if "--write" in sys.argv:
        from margrete_rpc import Margrete

        with Margrete() as m:
            base = snap_t(m.current_tick())
        n = write_chains(offset_chains(chains, base), color="red")
        print(f"已在 t={base} 写入 {n} 条 AirCrush（撤销用 m.undo()）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
