# 技术笔记：用 AirCrush 装饰线在 UMIGURI 里播放视频

这份笔记记录了开发过程中实测得到的全部规律、方案的演进，以及每个工具的用途。使用方法见仓库根目录的 README。

**最终方案**：link top，31×25 黑白两档，h 0~240，15 fps（第 7c 节）。全曲 3485 帧，7638 个 AirCrush，约 76 万个控制点，21097 个速度事件。

> 笔记里出现的“实例 0514、6882”等 4 位数字是开发时 Margrete RPC 的实例 ID，只用来区分不同的测试谱面。

---

## 1. 环境

| 项目 | 版本 / 说明 |
|---|---|
| Margrete（制谱器） | v1.8.0 |
| UMIGURI（模拟器） | UMIGURI NEXT |
| RPC 插件 | [margrete-rpc](https://github.com/Foahh/margrete-rpc) v1.2.0：`plugins\margrete_rpc.dll` + `.ini` |
| Python | 3.13，`pip install margrete-rpc==1.2.0` |
| ffmpeg | 在 PATH 里即可 |
| 视频 | Bad Apple!! PV：1920×1080，30 fps，6964 帧，232.13 s；4:3 内容区在 x=240..1680 |
| 谱面 | BPM 138，4/4，offset 0；音频 232.58 s |

**Margrete** 是 UMIGURI 官方配套的制谱器，导出 `.ugc` / `.mgxc`，支持 AIR-CRUSH 等音符和多条时间轴变速（TIL）。

**margrete-rpc** 由两部分组成：Margrete 里的 Rust 插件，以及 Python 客户端。两者通过 Windows 命名管道 `margrete-XXXX` 通信。

每次启动 Margrete 后要做的事：
1. 只需做一次：菜单「扩展功能 → 管理扩展」，勾选 `margrete_rpc.dll`。
2. 每次重启都要做：菜单「扩展功能 → Margrete RPC」，启动服务端。对话框里会显示 4 位实例 ID。

---

## 2. RPC 用法要点

```python
from margrete_rpc import Margrete, list_instances
from margrete_rpc.chart.events import TimelineSpeedEvent
from margrete_rpc.chart.notes import R

print(list_instances())                       # 开了多个 Margrete 时，用实例 ID 选择
with Margrete("0514", timeout=900) as m:
    with m.open_edit(raw_notes=True, event_scan_til_ids=[0]) as tx:   # 读快照，改完自动 diff 提交
        tx.chart.notes = [n for n in tx.chart.notes if ...]
    with m.open_edit(snapshot=False, raw_notes=True) as tx:            # 只追加，不读谱面（快）
        tx.chart.tils.append(TimelineSpeedEvent(til=1, t=0, speed=0.0))
        root = R.air_crush_begin(t=100, x=3, w=1, h=80, gap=0, color=10, til=1)
        root.children.append(R.air_crush_end(t=105, x=3, w=1, h=80, til=1))
        tx.chart.notes.append(root)
```

- 一个 `open_edit()` 块正常结束，就提交为**一次**可撤销编辑；块内抛异常则什么都不提交。
- 撤销用 `m.undo()`，**不要用 Margrete 界面里的 Ctrl+Z**（会产生重复音符）。
- 设置音符的时间轴（`til=`）要用 `raw_notes=True` 和 `R.*` 构造器。
- 颜色编号（从实例 0954 第 23 小节实测）：**白 = 10，黑 = 11**。`gap=0` 是 AirTrace 式的纯线条。
- `BeatEvent` 的字段是 `bar / beats_per_bar / beat_unit`，没有 `t`。

**坑**

| 现象 | 原因 / 对策 |
|---|---|
| 删掉的速度事件比预期少 | 事件是“扫描”出来的，默认只扫到最后一个音符之后 19200 tick。音符删光后就扫不到后面的事件。要传 `event_scan_lookahead_ticks` |
| `begin_edit_request` 超时 | 一次扫描太多条 TIL（151 条会超时），或者谱面太大（45 万条）。对策：每次只扫 4~8 条 TIL，并设 `Margrete(..., timeout=900)` |
| 纯追加很慢 | 用 `open_edit(snapshot=False)`，不读谱面，只发送新增内容。45 万条分 23 批写入，每批约 2 秒 |
| Margrete 很卡 | 编辑器要把所有线都画在时间轴上。UMIGURI 播放时不卡 |
| `m.undo()` 或提交时报 `failed to commit undo recording` | 实例 0954 上遇到过：报错后编辑可能已经生效，也可能只生效了一部分。之后一定要重新读一遍谱面确认实际状态。重要谱面先保存再用脚本改 |
| 读谱面时报 `pipe read failed: (109, ...)` | 单条 RPC 消息上限 **16 MB**（`margrete_rpc/_rpc/framing.py: MAX_FRAME_SIZE`）。谱面超过约 30 万条后，`open_edit()` 读快照会超限，插件直接断开管道（Margrete 本身没事，ping 正常）。14.5 万条时还能读 |

---

## 3. 几何规则（所有写入必须满足）

- **384 分割**：全音符 1920 tick ÷ 384 = **5 tick**。所有控制点和速度事件的 tick 都必须是 5 的倍数。
- **严格单调**：同一条 AirCrush 里，控制点的时间严格递增（`t_i < t_{i+1}`）。
- **直线性质**：控制点 i 对应空间点 `(t_i, c_i, h_i)`，其中 `c = x + w/2` 是 note 宽度的中点。相邻两点之间连三维直线段。
- **点限制**：轨道宽 16，`w ≥ 1` 且为整数，`0 ≤ x`，`x + w ≤ 16`。因此中点 c 只能取 **0.5, 1.0, …, 15.5，一共 31 个位置**。
  - 半格（如 3.5）用 `w=1, x=c−0.5`；整格（如 4）用 `w=2, x=c−1`。
- **高度**：h 最低 0（贴轨道）。判定线上**屏幕上沿 = h 240**（`test_calib.py` 实测；早先 0954 的标定 220 留了余量）。h 超过 240 的部分在屏幕外，看不见。
- 图案在时间方向上回头的地方必须切成多条链（`aircrush_kit.polyline_to_chains` 会自动切）。

---

## 4. UMIGURI 的绘制规律（实测）

设某条 TIL 的累计卷动量为 `X(T) = ∫ speed dT`。音符上时刻 t 的那个点，在当前时刻 τ 的**显示深度**为：

```
D = X(t) − X(τ)        D > 0：判定线前方（远处）  D = 0：判定线上  D < 0：判定线后方（玩家一侧）
```

| 规律 | 证据 |
|---|---|
| 0x 段上**纯横向**的线段（h 不变）看不见。（最初以为 0x 里所有线都看不见，后来 `test_zero.py` 证明竖线、斜线都看得见） | 第一版：横向线放在 0x 段里，什么都看不见 |
| 5 tick、1x 的线段长度为 5，看得见；两端 h 不同的竖线、两端 x 不同的线都看得见 | 点阵版、按列合并版都能看见 |
| **还没经过判定线**的部分：在前方可视范围内才画；退到判定线后方只画**很短一段**，再远就不画 | 诊断测试：深度 0 时正常显示，深度 −20 时瞬间消失；用户补充“后方只画很小一段” |
| **已经被判定线吃掉**的部分会继续画，判定线到玩家之间的那一段也画 | behind / emerge 方案的叠影 |
| AirCrush 线是**半透明**的，两幅画面重叠时会闪 | overlap 方案 |
| Margrete 能保存负速度和很大的速度（5×10⁶ / tick 量级） | 读回一致；park 方案实测正常 |
| **TIL 编号只能是 0~63**。用 1000+ 时，UMIGURI 里画面会消失 | pertil 方案在第 53 小节画面消失 |
| **0x 段里：竖线、斜线和 1x 一样清楚；纯横线（h 不变）看不见**。1x 时横线很细但看得见。推测 AirCrush 是沿 x 方向展开的带子 | `test_zero.py` V1~V4 |
| 0x 里改变 h 的连接段（斜线）**看得见** | `test_zero.py` V5 |
| **UMIGURI 的开销按音符（AirCrush）个数算**，与它在不在画面里无关；一个 AirCrush 里有多少控制点影响很小 | 性能对照：45 万个远处空音符 → 约 3fps；同样多的线段串成 1.4 万个 → ≥30fps；4.2 万个速度事件 → 无影响 |
| Margrete 插入速度事件的耗时增长得比数量快：62 万个时，写到 30 万就超时（900 s） | 6882 上的 `--chain` 尝试 |
| Margrete 在已有大量速度事件的谱面上删除事件也极慢（7363 上删 4.2 万个，十几分钟没删完）；写入 4.2 万个到空谱面只要几秒。**重新生成时一律写到新开的空白谱面上，不要原地重写** | 7363 |
| 0x 里从判定线往后方（玩家侧）移动的线，判定线后方那一小段会画出来：在黑白交界处能看到朝玩家侧的残线 | `--mode link --link all` 实测，放弃 |
| UMIGURI 在最后一个判定音符结束约 **10 s** 后停止画面，之后的线条不再显示。存放区放在结尾音符 10 s 以后就看不到。存放在那里的画面在视频播放时照常显示 | 用户实测；`--store-gap` 默认改为 13440 tick（约 12.2 s） |

**由此得到的原则**：视频播放期间，任何画面都**不能被判定线吃掉**。不显示的画面，要么停在可视范围之外的远处，要么停在判定线后方足够远的地方。必须经过可视范围或判定线附近时，要用极高的速度一下子掠过去。

---

## 5. 帧率与时间换算

```
每秒 tick 数   R = BPM × 480 / 60 = 8 × BPM = 1104        （BPM 138）
每秒网格数     G = R / 5 = 220.8                           （1/384 格）
固定间隔 k 格  fps = 220.8 / k     例：k=8 → 27.6，k=12 → 18.4，k=24 → 9.2
任意帧率       第 i 帧起点 S_i = 5 · round(i · 220.8 / fps)   （平均精确，抖动 ≤ 2.5 tick ≈ 2.3 ms）
```

30 fps 时，帧间隔在 35 和 40 tick 之间交替，和原视频一帧对一帧。

**音画同步**：把视频音轨和谱面 ogg 做互相关，ogg 比视频**晚 0.263 s**（≈290 tick），两次测量结果相同。
- 取帧公式：`视频帧号 = round((S_i / 1104 − 0.2629) × 30)`。帧号为负时画黑帧，相当于在视频前补了 0.263 s 黑场。
- 补完之后，第一个声音落在 1.741 s，正好是第 2 小节的小节线（1920 tick = 1.739 s）。
- 0.263 s 本身不在 5 tick 网格上，但它只用来选帧，不会变成谱面节点。

---

## 6. 方案演进

| 版本 | 做法 | UMIGURI 结果 | 原因 |
|---|---|---|---|
| v1 | 每行同色一段画成横向斜线，放在 0x 段里 | 看不见 | 0x 段长度为 0 |
| v2 | 每个像素一条 5 tick 直线，点段 1x；单 TIL，1000x 跳帧 | 能看见，但会**前后抽动、闪空白** | 跳帧期间旧画面已走、新画面未到；每帧整幅画面“被吃短又复原” |
| v3 overlap | 两条 TIL 交替；新画面先到，旧画面后被吃掉 | 不跳帧，但**两帧重叠** | 旧画面被吃掉的 5 tick 和新画面叠在一起，线条半透明 |
| behind / emerge | 用负速度让旧画面退到判定线后方再吃掉 | 仍然叠影 | 已经被吃掉的部分在判定线后方也会画 |
| store | 画面全部放在视频之后，视频期间不吃画面；旧画面退场时往判定线后方退 500 | 还能看到**往玩家侧闪** | 退场速度只有 100/tick，慢慢经过判定线后方“还会画的那一小段” |
| pertil | 每幅画面单独一条 TIL（1000~1149） | 第 53 小节画面消失 | TIL 编号超出 0~63 |
| park | TIL 1~63 轮流；不显示时停在 500 万远处；切换全部以约 10⁶/tick 掠过 | 完全没有瑕疵，但完整版只有约 3fps | 45 万个 AirCrush |
| park --chain | 一帧同色的线串成一个 AirCrush，线段 +1x/−1x 交替，0x 连接段 | 连接段是斜线，看得见；速度事件 62 万个，Margrete 写不进去 | |
| zero | 画面整个放在 0x 里；竖线按格子边界画；欧拉路径串链，换列只走横线 | 没有瑕疵，比 park 更好 | 全曲约 6.2 万个 |
| zero --double | 同上，每条线来回画两遍 | 最清晰，但 UMIGURI 里约 10fps | 全曲 23496 个 |
| link all | 同一 TIL 同色的全部画面串成一个 AirCrush；被包围的色块经判定线后方进出 | 残线可见（放在色块上沿 `--stub-at top` 也一样）；只有 126 个 AirCrush 仍约 11fps：此时开销主要来自控制点数（约 158 万） | 全曲 126 个 |
| link top | 只经顶边上方（屏幕外）串联；被包围的色块各自单独一个；画面上沿对齐屏幕上沿 h=240，25 行，视频左右裁成 1320×1080 保持比例 | 没有瑕疵 | 全曲 15114 个（30 fps） |
| **link top，15 fps** | **同上，帧率减半** | **最终成品** | **7638 个 AirCrush，76 万个控制点** |
| link top --gray | 每格两层：白白 = 白，白黑 = 灰，黑黑 = 黑（三档灰度） | 灰色效果不好，未采用 | 4404 个 |

点阵（31×23）和按列合并的画质看不出区别，但按列合并的条数只有点阵的约 1/11。

---

## 7. park（第一个无瑕疵方案，调度沿用至今）

**画面编码**：31 列（c = 0.5…15.5）× 23 行（h = 0, 10, …, 220）。视频先裁出 4:3 内容区，再用 area 插值缩放，阈值 128 二值化。每一列里同色的连续像素画成一条 AirCrush：

```
(P, x, w, h_top) → (P+5, x, w, h_bot)      白 = 10，黑 = 11，gap = 0；该 5 tick 的速度为 1x
```

**调度**：第 i 帧放在 `TIL = 1 + (i mod 63)`，是这条 TIL 上的第 `m = i div 63` 幅画面。

- **存放区**：所有画面都放在视频结束之后的存放区。第 m 幅的线段在 `Q_m = Z + 5 + 10m`，并且 `X(Q_m) = m·Δ`，其中 Δ = 5 万（`--spacing`）。
- **视频期间**，每条 TIL 的卷动量 X 只取两种值：

| 状态 | X | 画面情况 |
|---|---|---|
| 停放 | `−D`（D = 500 万，`--far`） | 这条 TIL 上的所有画面都在 ≥ D 的远处 |
| 显示第 m 幅 | `m·Δ`，速度 0 | 第 m 幅深度为 [0, 5]；同 TIL 的其他画面在 ≥ Δ 的前方或后方 |

- **切换只用 5 tick**：
  - 到场 `[S_i − 5, S_i]`：X 从 −D 到 m·Δ，画面 m 从远处飞来。
  - 退场 `[S_{i+1}, S_{i+1} + 5]`：X 从 m·Δ 回到 −D，画面 m 飞回远处。方向朝前方，不进入判定线后方。
  - 两个过程的速度都约为 `(m·Δ + D)/5 ≥ 10⁶ / tick`。
  - 新画面到场和旧画面退场发生在两条不同的 TIL 上，交接点都是 S_{i+1}。
- **视频结束后**：所有 TIL 同步把第 0、1、2… 幅依次吃掉，每幅 10 tick。被吃掉的部分留在 ≥ Δ 的后方，看不到。

**模拟结果**：画面在“可视范围或判定线附近”掠过的总时长约为每帧 0.009 ms（假设可视距离 3000）或 0.09 ms（假设 30000）。60 Hz 刷新时一帧是 16.7 ms，所以看不出来。

如果以后看到闪，可以把距离加大 10 倍（`--far 50000000 --spacing 100000`），掠过的时间会再缩短到原来的 1/10。代价是卷动量的数值也变成 10 倍。

### 7b. zero --double

调度（哪帧用哪条 TIL、到场 / 退场、停放距离）和 park 完全一样，区别只在画面怎么存：

- **整幅画面在 0x 里**：第 m 幅画面的全部控制点都在 `X = m·Δ` 的 0x 段内，不再需要 1x 点段。所以速度事件和 park 一样多（全曲 42013 个）。
- **竖线按格子边界画**：每列同色的连续格子画成一条竖线，从格子下边界画到上边界（边界 `h = round(220·k/23)`，k = 0..23）。单格也有高度，不会在 0x 里消失。
- **串链（`euler_paths.py`）**：把 24 个边界高度当成点，每条竖线当成边。同一高度上任意两列之间可以用纯横线随意跳（0x 里看不见）。
  - 只画一遍：需要的笔数 = 每个连通块 max(1, 奇数度高度数 / 2)，全曲 61631 个 AirCrush。
  - `--double`：每条线来回各画一遍，所有高度都是偶数度，每个连通块一笔，全曲 **23496 个**（平均 3.4 个/帧）。两层叠在一起，透明度均匀，看起来更清晰。
- **存放区**：第 m 幅画面占 `[q, q + 5·(P−1)]`，P 是这一帧最长那一笔的控制点数，相邻画面之间用 5 tick 跳到下一个 `m·Δ`。全曲存放区长约 94840 tick（86 s）。它从视频结束后 13440 tick（`--store-gap`，约 12.2 s）开始，此时 UMIGURI 已经停止画面，所以看不到。
- 校验（`check_zero.py`）：每个 AirCrush 的控制点都在同一卷动量，没有斜线段，6971 帧的竖线与原画面逐格一致。

```
python badapple_air.py --write --instance <ID> --mode zero --double --rows 23 --htop 220 --aspect stretch --fps 30 --seconds 232.396
```

### 7c. link top（最终方案）

在 zero --double 的基础上，把同一条 TIL 上全部画面的同色笔画串成一个 AirCrush（`zero_link.py`）：

- 每幅画面每种颜色按高度连通分组。碰到顶边的那一组从顶边进出：从 h=H（`--sky`，默认 5000）竖直下到顶边，画一个欧拉回路回到顶边，再上到 H。在 H 高度移到下一幅画面，这些都在屏幕外。
- 不碰顶边的组（被另一种颜色包围的色块）各自单独一个 AirCrush。`--link all` 会让它们经判定线后方进出，但会留下可见的残线，所以不用。
- **画面上沿必须正好在屏幕上沿（h=240）**，否则从顶边往上走的线会露出来。所以用 `--rows 25 --htop 240 --aspect crop`。格子大小和 23 行 / 220 时基本相同（每行 9.6），视频从 4:3 区域左右各裁 60 像素（1320×1080）以保持比例。
- 同一 TIL 的白、黑两条链在时间上先后错开，每个时刻只有一个 X。速度事件只在速度变化处写入（`compress_events`）。
- 校验：`python tools/check_link.py 秒数 top 25 240 top 15`（分段类型、逐格覆盖）。

```
python badapple_air.py --video badapple.mp4 --seconds 232.396 --write --instance <空谱面 ID>   # 默认参数就是最终方案
```

30 fps 时全曲：15114 个 AirCrush，152 万个控制点，42013 个速度事件。结尾是 Damage 音符（`--end-type damage`，默认）。

**性能结论**：AirCrush 很多时，开销按个数算（45 万个约 3fps）。个数降到几百以后，开销按控制点数算（158 万个控制点约 11fps）。所以最后用 `--fps 15` 让控制点减半：全曲 3485 帧、7638 个 AirCrush、76 万个控制点，这就是最终成品。

---

## 8. 写入时的注意事项

- **结尾音符**：视频最后一帧必须放一个 Damage 音符（`--end-type damage`，默认），否则 UMIGURI 会提前结束游戏画面，因为装饰线（gap=0）不算音符。旧做法是一条 h=1000、带判定的 AirCrush（`--end-type aircrush`），也可以。
- **存放区**：UMIGURI 在最后一个音符约 10 s 后停止画面。存放区默认放在视频结束 13440 tick（约 12.2 s）之后（`--store-gap`），所以看不到。
- **写到空白谱面**：每次重新生成都写到一张新开的、BPM 138 的空白谱面上。在已有完整版的谱面上原地重写，删速度事件会非常慢。
- **手动保存**：写入后在 Margrete 里手动保存，RPC 没有保存接口。
- **没法读回校验**：完整版超过 RPC 单条消息 16 MB 的上限，读不回来，只能以写入日志为准。正确性在写入前用 `tools/check_link.py` 校验。
- 如果一定要清空一张大谱面，可以先不读快照、整体替换掉全部音符，等谱面变小后再删速度事件：

```python
from margrete_rpc import Margrete
with Margrete("<ID>", timeout=900) as m:
    with m.open_edit(snapshot=False, replace_all_notes=True) as tx:   # 删掉【所有】音符
        pass
    for k in range(1, 64, 8):                                          # 再按 8 条 TIL 一组删速度事件
        ch = list(range(k, min(k + 8, 64)))
        with m.open_edit(raw_notes=True, event_scan_til_ids=ch, event_scan_lookahead_ticks=450000) as tx:
            tx.chart.tils = [e for e in tx.chart.tils if e.til not in ch]
```

---

## 9. 工具清单

| 文件 | 作用 |
|---|---|
| `badapple_air.py` | 主生成器：视频 → AirCrush 帧动画。默认只预览和校验，加 `--write` 才写入 |
| `zero_link.py` | link 模式：同 TIL 同色画面串链、三档灰度（`--gray`） |
| `euler_paths.py` | 按列取同色连续格子（`column_runs`）、格子边界高度（`edges_h`）、欧拉路径分笔（`trails`） |
| `aircrush_kit.py` | 通用 AirCrush 折线工具：吸附到 5 tick / 0.5 中点网格、`center_to_xw`、校验、按时间方向自动切链 |
| `tools/check_link.py` | link 模式校验：分段类型（只允许内容竖线 / 横跳 / 屏幕外）、逐格覆盖。`python tools/check_link.py 秒数 top 行数 htop top fps [gray]` |
| `tools/check_zero.py` | zero 模式校验 |
| `tools/test_zero.py` | 诊断：0x 里竖线 / 横线 / 斜线 / 串链是否可见（V1~V6） |
| `tools/test_calib.py` | 标定：判定线上屏幕上沿对应的 h |
| `tools/test_perf.py` | 性能对照：空速度事件、远处空音符、串链空音符 |
| `tools/test_negative.py` | 诊断：一幅静止图移到不同深度（含负深度）时 UMIGURI 怎么画 |

**`badapple_air.py` 主要参数**

| 参数 | 默认 | 说明 |
|---|---|---|
| `--video` | `badapple.mp4` | 视频文件（也可以用环境变量 `BADAPPLE_VIDEO`） |
| `--audio-lag` | 0.2629 | 谱面音频比视频晚多少秒，用来对齐取帧 |
| `--mode` | link | link / zero / park；其余是早期失败方案，仅作对比 |
| `--link` | top | top：只经屏幕上方串联；all：被包围的色块经判定线后方进出（有残线） |
| `--seconds` / `--start` / `--anchor` | 5 / 0 / 0 | 时长（秒）、谱面起始 tick、视频 0 点所在的 tick |
| `--fps` | 15 | 任意帧率，按 `S_i = 5·round(i·220.8/fps)` 排帧 |
| `--rows` / `--htop` | 25 / 240 | 行数与画面上沿高度（240 = 屏幕上沿） |
| `--aspect` | crop | crop：左右裁掉一些以保持比例；stretch：直接缩放 |
| `--far` / `--spacing` | 5e6 / 5e4 | 停放距离 D、同 TIL 画面间距 Δ |
| `--store-gap` | 13440 | 视频结束到存放区的间隔 tick |
| `--tils` | 1..63 | 轮流使用的时间轴（必须在 1..63，0 留给正常谱面） |
| `--colors` | both | both / white / black |
| `--threshold` | 128 | 二值化阈值 |
| `--gray` | 关 | 三档灰度（效果不好，仅供参考） |
| `--end-type` | damage | 结尾音符：damage / aircrush |
| `--instance` / `--write` | | 目标实例；不加 `--write` 时只预览 |

---

## 10. 下一步可以做的

- **彩色视频**：把阈值二值化换成按调色板量化。Margrete 共有 15 种颜色（DEFAULT、RED、ORANGE、YELLOW、GREEN、SKY、BLUE、VIOLET、PINK、WHITE、BLACK、GRASS、SKY_BLUE、COBALT_BLUE、PURPLE），合并时改成“同色连续段”即可，调度不用改。
- **只画白**（`--colors white`）：条数约减半。前提是背景本来就暗。
- **降低分辨率**：控制点数大致和格子数成正比，减少行列可以进一步提高帧率。
- **和正常谱面共存**：正常音符放在 TIL 0，视频只占 TIL 1~63，两者互不影响。
