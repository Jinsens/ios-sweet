# matisse CPU 调度与性能审计（2026-11-13）

结论：有优化空间。调度方面最明确的缺口是缺少大小核容量与交互任务放置策略；
当前最直接的性能证据则是显示路径的长时间忙轮询。CPU 已全部启动，三个性能域
也已请求最高档位，不能再把“打开大核/提高频率”当成尚未完成的优化。

## 范围与证据

- 当前设备为 MT6983：4×A510、3×A710、1×X2，仓库标题中的 SM7150 是历史目标。
- 核对 2521 冻结内核、组合构建树与现有 2518/2520/2521 日志；没有运行设备命令。
- `sched_prim.c`、`sched_clutch.c`、`arm64/machine_routines.c`、`sched_prim.h`、
  `arm64/proc_reg.h`、`SWEET.h` 在默认和 2521 组合构建树中 SHA256 全部相同。
  `nm` 检查 2521 内核存在 `_sched_clutch_dispatch`。
- 原始日志、构建树和 SHA 清单留在忽略目录：
  `research/matisse/20261113-cpu-scheduler-audit/evidence.json`。
  本文只摘录性能数据，不提交设备转储或第三方源码。
- 2521 日志是另一个并行工作流产生的现有文件；这里只据其核启动/档位记录作判断，
  不据此宣称该试验完成、分区已恢复或性能有提升。

## 1. 调度器知道 E/P 名称，但没有按大小核性能选核

已确认的代码事实：

1. [mkadt.py](../tools/mkadt.py#L175) 把 CPU0–3 标为 E，CPU4–7 标为 P，
   A710 和 X2 合在 P 级。这些属性用于缓存和 `hw.perflevel*` 报告。
2. [0089 patch](../xnu/patches/0089-sweet-hetero-cache.patch#L119) 强制
   `pset_cluster_id=0`，所有核心共用一个处理器集合。SWEET 未启用 AMP；
   实际使用非 AMP 的 Clutch 调度器。
3. 实际源码 `osfmk/kern/sched_prim.c:5080` 优先复用上次的空闲核心；
   `:5242` 对空闲核位图做 `lsb_first`，没有容量比较。
   CPU0–3 编号较小，因此无可复用 hint 时优先选中的空闲核可能是 A510。
4. `osfmk/arm64/machine_routines.c:1984` 的平台选核函数原样返回 hint。
   默认 urgency 回调在 `:1951` 为空，没有板级性能策略接入。

这证明存在偏向低编号小核及继续复用小核的机制，**尚不证明当前 UI 线程的实际
每核运行比例**。旧笔记依据小核频率变化与 GL 耗时变化作过推断，仍需逐线程统计。

推荐先记录 backboardd、SpringBoard、Mesa 调用线程和显示 workloop 的每核运行时间、
可运行到执行延迟、迁移次数及 QoS/Clutch bucket，再做可关闭的交互任务偏好实验：
交互任务优先可用 P 核，后台工作优先 E 核，保留绑定、实时任务、核心状态及回退规则。

**只改空闲核选择顺序并不完整。** `sched_clutch.c:2946` 把符合条件的未绑定线程放入
同一 shared root，`:2930` 允许各核从该 root 取任务。唤醒大核不保证目标线程一定
在大核执行；需要同时设计取任务、迁移/再平衡规则，验证后台吞吐与饥饿情况。
不建议永久把所有 UI 线程绑到 CPU7，或仅调整时间片就宣称解决大小核调度。

完整 AMP/Edge 可作为后续工程，但不是增加一个 `sched=edge` 启动参数：需要建立
E/P psets、解除 pset0 强制、实现默认放置策略，并验证迁移、偷取和性能控制回调。
当前 `arm64/proc_reg.h:87` 还把 Edge 的自动启用限定在 AMP + OSX 配置。

## 2. 显示忙等和 vsync 共用 workloop，已有超帧预算证据

[disp_hold_frame](../xnu/display/sweet_matisse_dsi.c#L361) 自帧启动 `began` 计，
至少保留 `sweetfb_hold_us=14500` μs，期间反复读取寄存器，没有线程睡眠；
[disp_te_wait](../xnu/display/sweet_matisse_dsi.c#L539) 同样轮询 TE。
ARM `yield` 指令不会让 XNU 线程进入睡眠。
[vsyncFired](../xnu/display/SweetFramebuffer.cpp#L638) 在同一个 workloop 调用
`present()`，返回后才重新挂定时器；通知节奏会受长回调影响。

2518 实机日志 `shim2518-ui/gpu-normal/after-console.txt`：

| 日志行 | present 次数 | present 总耗时 | 每次平均 | vsync 平均间隔 |
|---|---:|---:|---:|---:|
| 4928 | 125 | 2,807,823 μs | 22.463 ms | 19.824 ms |
| 5687 | 97 | 2,169,162 μs | 22.362 ms | 19.052 ms |
| 5705 | 0 | 0 | — | 16.694 ms |

前两个窗口的 TE 等待平均约 7.5 ms；对应日志的累计 hold 平均约 14.7 ms。
这些是**墙钟时间，含抢占**，不能直接等同于实际 CPU 执行时间；源码确认等待采用
忙轮询。22 ms 已超过 60 Hz 的 16.67 ms 帧预算。旧 2514 的“13.5 ms present、
9.5 ms hold”不能用来描述当前无闪烁配置。

优先方向：保持 vsync 通知节奏，把长等待移至专门 worker/定时完成状态机或可靠的
硬件完成 IRQ；TE 到发起的关键窗口仍须满足新鲜度/300 μs lead 约束。
仅把忙等换为 sleep 能减少 CPU 占用，**不能自动缩短整帧耗时**；还需验证通知、
准备和传输是否可以合理重叠。

必须保留 `12cc1cc` 的 surface in-use 和 `d8674c9` 的 pending/inflight serial
完成语义，以及 `syncswap=1 vendor=1 hold_us=14500 lead=300` 的视觉基线。
直接关同步或缩短 hold 会改变已验证的防闪烁条件。

## 3. CPU 已请求满频；首次提频仍有 66 ms 锁内忙等

[sweet_cpu_clock_fastest](../xnu/gpu/SweetGPU.cpp#L75) 默认在第一次 GPU PING
执行。现有 2521 日志第 100 行记录 `8/8 CPUs initialized, SGI1 verified`，
第 2139/2141/2143 行记录三域 `enable=1/index=0`，LUT 对应
**1800 / 2850 / 3050 MHz**。这是请求档位的寄存器读回，不是独立物理频率测量。
shim 的 GPU DVFS off 不代表 CPU 没有提频。

第一次 PING 持有 `gpu_lock`，三个域各执行 2 ms + 20 ms 的 `IODelay`，
`IODelay` 名义等待合计 **66 ms**，另有映射、日志及抢占开销。
可将调频移到有明确 readiness 的平台初始化阶段或锁外初始化。
这改善首次图形工作/启动延迟，不应算作持续 FPS 增益。

长期还缺板级按负载调频和真实 SoC 温度反馈。应测持续帧耗与温度，再设计按簇策略；
现有证据不支持把某次卡顿直接归因于热降频。ADT 全核写死 2 GHz，
`hw.cpufrequency*` 也不能作为运行时频率证据。CPU halt/quiesce 仍会 panic，
热插拔与深度休眠需另行实现；普通空闲路径已有 WFI。

## 4. 后续候选：GPU 等待、资源同步和诊断调用

- [GPU fence wait](../xnu/gpu/mali_csf_runtime.c#L168) 仍轮询 extract/fence 和
  故障状态，barrier 回调为 `dsb sy`。2520 的 9800 次 wait 平均 459 μs，
  累计约 4.50 秒墙钟等待。可研究短轮询后睡眠/完成 IRQ，保留 fence、extract、
  故障、超时重试和门铃恢复规则。并行 GPU 工作流已在处理报告字段的重复读取，
  本审计未改动该实现。应先评估 2521 transfer 异步修复，不能将全部 wait 归于队列满。
- [transport](../userland/sweet-panthor-drm.c#L322) 的 BO close、VM unmap、
  group destroy、surface 元数据查询会 drain 全部 pending work。先按原因统计，
  再考虑按资源最后使用 fence 缩小等待范围，保证 GPU 访问期间 backing 不被释放。
- [PING](../userland/sweet-panthor-drm.c#L65) 在 trace 关闭时仍重复跨内核调用。
  将唯一的首次提频入口迁出后，可按开关关闭后续诊断 PING；收益需实测。
- 当前图像使用 `ufslogsecs=1 glframes=1`；UFS 日志落盘与根文件系统共用存储锁。
  可比较日志批处理/采样。现有 `ufswait=1` 提供短轮询后睡眠，需同时测 I/O 延迟，
  不能只看 CPU 降低。目前尚无证据把存储读写列为首要 UI 瓶颈。

## 建议实施顺序与验收

1. 以已修复显示闪烁的配置为基线，固定分辨率、交互/基准负载和采样区间。
   结合 2521 GPU 验证记录，统计每帧 submit/wait 数和等待原因。
2. 增加可关闭、批量输出的逐线程 CPU/唤醒延迟统计；测 P50/P95/P99 帧耗、
   超 16.67 ms 比例、后台吞吐。避免逐次切换写日志污染结果。
3. 优先改显示长等待/通知隔离；单独比较大小核任务放置，便于归因。
4. 再做按资源同步、GPU 等待机制、首次提频和诊断调用清理；最后补持续性能管理。

显示改动同时检查 surface 生命周期、TE miss/late、hold trunc、underrun、
syncswap 超时及用户观察到的闪烁；GPU 改动检查 fence 正确性、fault 和超时重试。
本次仅完成静态审计和既有日志复算，未新增内核构建、CI 或设备试验，未宣称 FPS 提升。
