# Redmi K50 Pro：XNU 实机进展与 iOS 启动路线

更新：2026-10-23，完成本轮 `vm-return-brk` 实机测试后。

**当前结论：这台 K50 Pro 已执行真正的 XNU，完成早期 MMU、正式页表、平台与 CPU/GIC 初始化，并进入 `kernel_bootstrap`；`vm_mem_bootstrap` 已完整返回，包括 `vm_page_bootstrap`、`zone_bootstrap`、`vm_object_bootstrap`、`vm_map_init`、`kmem_init`（含 KMEM/ZALLOC 启动阶段和 `zone_init`）与 `vm_fault_init`。尚未进入用户态，也尚未启动 iOS 的 launchd 或桌面。**

本轮设备自动返回 Recovery；原始 `boot_a` 的全部 67,108,864 字节已恢复并读回校验。本轮没有写入 `userdata`。详细逐次记录以 [NOTES.md](../NOTES.md) 顶部 ACTIVE 区为准，下面历史记录可能代表已经被后续证据替代的判断。

## 1. 项目是什么，哪些成果可以复用

本项目将 Apple 开源 XNU 8796 系列移植到非 Apple ARM64 手机，再提供 iOS 用户空间需要的文件系统、系统调用、IOKit 服务和设备驱动。它运行独立内核，不依赖 Android 内核承载 iOS 进程。

需要严格区分两个目标设备：

| 项目 | 原目标 sweet | 当前在线设备 matisse |
| --- | --- | --- |
| 手机 | Redmi Note 10 Pro | Redmi K50 Pro |
| SoC | Qualcomm SM7150 | MediaTek MT6983 / Dimensity 9000 |
| 既有记录 | README、历史 NOTES 记录了 iOS 桌面和触摸成果 | 本次实测到 XNU zone/object 初始化 |
| 临时启动 | 原设备支持 `fastboot boot` | 本 LK 没有可用的 `fastboot boot` |
| 显示 | 1080×2400，连续扫描路径 | 1440×3200，DSC 命令模式，需要主动刷新 |
| 触摸 | Goodix，经 Qualcomm GENI I²C | ST FTS 路线，经 MTK SPI6，见第 6 节 |
| USB | Qualcomm 包装层内的 DWC3 | MediaTek MTU3 / SSUSB，另有 xHCI 主机路径 |

原机型的用户态兼容层、HFS+、Mach-O 加载、IOSurface、IOMobileFramebuffer 和 HID 接口值得复用；硬件寄存器、时钟、电源、DMA 和中断实现必须按 matisse 重做或验证。原机型的桌面成功记录不等于当前设备的桌面成功。

主要代码入口：

| 路径 | 作用 |
| --- | --- |
| `shim/board.c`、`shim/fdt.c` | 从 live FDT 获取板级资源 |
| `shim/loader.c`、`tools/mkpayload.py` | 装载 XNU Mach-O、生成 boot_args、放置 ADT 和可选 RAM disk |
| `tools/mkadt.py` | 构造 XNU 使用的 Apple Device Tree；提供 matisse 配置 |
| `tools/mkentryprobe.py` | 同次启动诊断包装器、断点与异常回传 |
| `xnu/patches/` | 内核、文件系统、平台服务和驱动补丁 |
| `.github/workflows/xnu.yml` | GitHub Actions 内核构建 |
| `tools/sweet-init.S`、`tools/mkinit.py` | 最小静态 ARM64 用户态 PID 1 |
| `tools/mkiosroot.sh`、`tools/sweet-root-tweaks.py` | 构建并调整 iOS 用户空间根镜像 |

## 2. 已经完成的全过程

### 2.1 从“只见 Redmi 标志”变成可定位的启动链

1. **确认硬件和恢复方式。** 识别实际设备为 matisse，保存启动分区等备份。LK 接受下载和刷写，但临时 `fastboot boot` 不可用，因此每次测试临时替换 `boot_a`，随后恢复。Recovery ramdisk 位于 `vendor_boot`，但启动仍依赖可工作的内核；不能认为坏的 `boot_a` 一定能直接进 Recovery。
2. **验证 LK 确实跳入自定义代码。** 复位 stub 和入口探针建立了这一点。随后用同次启动探针确认真实入口为 EL1、13 MHz 计时器、GICv3；不能根据 LK 的 `via EL2` 字样判断实际异常级别。
3. **修正板级资源与镜像布局。** 适配 boot header v4、live FDT、四单元 GIC timer 描述、正确 framebuffer、上部 RAM 和嵌入内核 Image 的 payload。修复只扫描对齐地址等问题。旧完整 shim 仍有未解释的失败，不能把这些修复直接等同于完整 shim 已成功。
4. **打通显示对照。** 由于面板是命令模式，只改显存不会自动更新。受控刷新测试中，用户看到了 `DISPLAY KICK` 与移动绿条，也看到了 EL1 测试文字。这证明在当前 LK 遗留状态下可以刷新面板。
5. **建立同次启动证据。** 自定义代码运行后，把阶段或异常写入所携带的原 Linux banner，再转入 Recovery，从 `dmesg` 读取 `SWEET-ENTRY`。这避免依赖未经证明能跨重启保留的 DRAM 日志。
6. **确认加载路径。** 只加载、不跳 XNU 的镜像成功完成段复制、ADT 和 boot_args 准备并返回。完整 shim 与无断点直接跳转曾只显示 logo，部分需要手动 fastboot 恢复；logo 本身不说明停在哪。
7. **逐步推进真实 XNU。** 在实际内核指令流插入受控 BRK，从入口一路推进至当前的 zone/object 初始化。每轮先主机检查、再真机执行、最后恢复并验证整个分区。

### 2.2 XNU 断点证据

以下均为真机命中预期位置、自动返回 Recovery、随后完整恢复的结果。`ESR=0xf2005357` 是主动插入的 `BRK #0x5357`；它不是此次发现的真实内核崩溃。PC 的低地址为物理执行地址，高地址为 XNU 虚拟执行地址。

| 检查点 | 镜像 SHA256 前缀 | 记录 PC | 已证明的执行范围 |
| --- | --- | --- | --- |
| XNU 入口 | `386420a8` | `10148c488` | 已真正跳入 XNU |
| 首次切异常向量之前 | `1291e310` | `10148e104` | 原始入口分支及初始汇编 |
| bootstrap 页表完成 | `276ffdd7` | `10148e444` | 早期页表构建 |
| MMU 配置完成 | `36228bdb` | `10148e4f8` | TCR、TTBR、MAIR 等配置 |
| MMU 开启后 | `7937d19c` | `10148e530` | 真实 MMU 开启和启动环境设置 |
| 早期 C 代码 | `98e8829d` | `fffffe0007ad58b0` | `arm_init` 及早期控制台标记 |
| `arm_vm_init` 调用前 | `25d5d260` | `fffffe0007ad6124` | VM 初始化前的平台准备 |
| 正式页表构建完成 | `340b9628` | `fffffe0007483dd8` | 构造正式页表 |
| 正式 TTBR1 切换后 | `e9fbcb81` | `fffffe0007483ddc` | 正式内核地址空间可执行 |
| TTBR0 清空后 | `c73eb57f` | `fffffe0007483e18` | 旧低地址映射清理、TLB 刷新、重定位后日志调用 |
| `arm_vm_init` 返回 | `9ac98004` | `fffffe0007ad6128` | 完整 ARM VM 初始化 |
| `serial_init` 返回 | `e9888fe6` | `fffffe0007ad6164` | 返回值 `x0=1`，控制台初始化成功 |
| 后续平台初始化 | `a89bd1c0` | `fffffe0007ad62d8` | `PE_init_platform(TRUE)` 等返回 |
| `machine_startup` 调用前 | `34efc91a` | `fffffe0007ad63a0` | CPU、计时器、GIC 上下文和 early_random 初始化 |
| `vm_page_bootstrap` 返回 | `e9b81b88` | `fffffe0007abfab0` | 已进入 `kernel_bootstrap` → `vm_mem_bootstrap`，完成物理页管理初始化 |
| `zone_bootstrap`、`vm_object_bootstrap` 返回 | `e4231b90` | `fffffe0007abfad0` | 在 `vm_mem_bootstrap` 内连续完成 zone 与 VM object 初始化 |
| 完整 `vm_mem_bootstrap` 返回 | `99d68ed6` | `fffffe00073644d8` | `vm_map_init`、`kmem_init`、KMEM/ZALLOC 启动阶段、`vm_fault_init` 全部返回，`kernel_bootstrap` 继续执行 |

`serial_init=1` 只证明函数返回成功；后期各轮没有收集用户的屏幕观察，不能宣称已经看到了 XNU 控制台。独立探针的 IRQ 测试曾成功处理 INTID 27，但当前 XNU 调度器启用中断后的持续运行还未验证。

### 2.3 为推进断点补齐的诊断能力

- MMU 关闭时的向量转发，以及 MMU 开启后的高地址异常回传。
- 恢复初始 `SPSel`、栈、SCTLR 和 Linux DTB 参数。
- 在返回 Linux 前显式清理 XNU 写过的缓存区域；最近一轮范围为物理地址 `[0x100000000, 0x108000000)`，共 128 MiB。
- XNU 清空 TTBR0 后，仅在异常回传时恢复 bootstrap TTBR0，保持原始初始化流程照常执行。
- 单独记录 x0，以区别函数“返回了”和“返回成功”。

主机验证包括 36 项工具测试、交叉编译，以及使用真实指令和实际生成页表的 Unicorn 模型。模型显式模拟 16K 地址转换、部分 MMIO、计数器、GIC 和异常入口；它不证明真实 cache coherence、IRQ 持续投递或硬件时序。仓库全量检查此前还有六个已有 CRLF shell 问题，不能声称全仓检查已通过。

## 3. 如何启动到用户态

**最短路径是先运行 RAM disk 中的最小 PID 1；这一步不需要 UFS、触摸或完整 iOS 根文件系统。** 当前测试 payload 没有 `RAMDisk` 节点，也没有根设备启动参数；放开当前断点并不会自动出现用户态。

预期顺序如下：

```text
已完成：arm_init → machine_startup → kernel_bootstrap
       → vm_mem_bootstrap → vm_page_bootstrap / zone_bootstrap /
         vm_object_bootstrap / vm_map_init / kmem_init / vm_fault_init
       → 完整 vm_mem_bootstrap 返回

下一步：恢复正常异常向量 → 调度器 / Mach IPC / task / thread / clock
       → kernel_bootstrap_thread → IOKit → 正常中断运行
       → bsd_init → md0 根文件系统 → exec 最小 /sbin/launchd
       → EL0 的 PID 1 输出心跳并持续存活
```

建议分成四个可验收阶段：

| 阶段 | 要做的事 | 通过标准 |
| --- | --- | --- |
| A：完成 VM | 先检查 zone/object 初始化，再完成 vm_map、kmem、vm_fault 等 | 命中 `vm_mem_bootstrap` 返回后的断点，未发生更早异常 |
| B：启动内核线程 | 推进 scheduler、task/thread、上下文切换和时钟 | 到达 bootstrap thread；在 XNU 正常异常向量下验证定时中断与线程运行 |
| C：挂载最小根 | 用 `mkpayload.py --ramdisk` 打包静态测试程序，使用 mockfs/md0；之后再试小 HFS+ RAM disk | `md0` 注册、根挂载、`load_init_program` 执行 |
| D：进入 EL0 | 使用现有 `sweet-init.S` 和 `mkinit.py`，验证代码签名、用户栈和系统调用 | 真机输出 PID 1，`open/write/getpid/select` 正常，多个五秒心跳持续运行 |

测试程序路径虽然叫 `/sbin/launchd`，内容是项目自己的静态 init。看到其心跳代表“用户态已跑通”，尚不代表 Apple launchd 或 iOS 已运行。该程序已处理真实 ARM64 对 SP 对齐的要求，不能随意删除这类只在硬件上暴露的修复。

有两处必须提前处理：

1. **当前诊断向量不能直接留到用户态。** 回传方案覆盖了异常入口；后续正常 IRQ、用户态 SVC 和缺页异常必须进入 XNU 自己的处理函数。应在开放中断前恢复正常向量，或另做只拦截指定诊断 BRK、其余入口正确转交的机制。进入线程调度、设备 DMA 或多核后，也不能默认还能无副作用地跳回 Linux。
2. **每个更深断点都要重新检查内存。** 本轮 vm_pages 的模型映射已写到约 `0x1038c16cf`，不能只看未继续增长的 `avail_start` 来估算清理范围。页表、异常代码、回传状态及 Linux 保留区域的存活都要复核。

旧交接把 `vm_retire_boot_pages` 当作可能释放 bootstrap 表的边界。本次核查发现：**当前 CI 内核中该函数只有一条 `ret`，源代码也是空实现**。所以它在此构建中不会自行回收页表；但后续分配、地址映射和向量切换仍需逐段验证，换内核后也必须重新检查。真正更晚的 bootstrap 段回收和 lockdown 不能忽略。

## 4. 如何真正启动 iOS

在这台设备上，目标是运行真实 iOS 用户空间程序，并用移植后的 XNU 和硬件服务支持它们。工程上可分为“Apple launchd 运行”“系统服务运行”“桌面可见”“桌面可交互”四个独立里程碑。

### 4.1 用户空间和文件系统

项目现有路线选择 iPhone X 的 iOS **16.7.16 / 20H392**，使用普通 `arm64` 用户空间。这一代 A11 用户空间不要求 `arm64e` 的指针认证 ABI，适合复用现有内核兼容工作；不能仅因 MT6983 CPU 更新，就随意替换成新机型、新系统的用户空间。

根镜像需要同一版本的 OS、SystemOS、AppOS、`/usr/lib/dyld`、`/sbin/launchd` 和完整的 dyld shared cache 分片。不能只复制 launchd：libSystem 等依赖主要位于 shared cache 中。历史资产是数 GiB 级别，不适合当前包含原 Linux 的诊断 boot 镜像。原 sweet 的 fastboot 容量限制也不能直接套到 matisse。

仓库采取 **从 APFS 读取文件、重新制作大小写敏感且带日志的 HFS+ 根卷** 的路线。XNU 已有内建 HFS 支持，无需先完成 APFS 移植。`mkiosroot.sh` 整合 cryptex 文件和共享缓存链接；必须验证实际文件、权限、所有者、硬链接、符号链接以及需要的元数据，并完成文件系统检查。该脚本面向 Linux 的 FUSE/loop mount 环境，不能原样视为 macOS 一条命令可运行。

### 4.2 分阶段验收

| 阶段 | 依赖与工作 | 应拿到的证据 |
| --- | --- | --- |
| Apple launchd | 最小用户态稳定；UFS 根读取可靠；dyld/cache、Mach、pthread、签名与 entitlements 兼容 | Apple launchd 成为 PID 1，正常装载库并启动子进程 |
| 系统服务 | 可写 `/private/var`、容器目录、共享内存、系统属性、KeyStore 等兼容服务 | 核心服务连续运行，崩溃能记录与符号化 |
| 桌面可见 | IOSurface、IOMobileFramebuffer/AppleCLCD、CoreAnimation 软件绘制、持续面板刷新 | backboardd、SpringBoard 运行，画面实际出现在手机上 |
| 桌面可交互 | ST 触摸、按键、坐标映射和 HID 事件 | 点击、拖动、抬起、多指、底部手势、锁屏唤醒分别验证 |

现有补丁包含 AMFI/entitlements、AppleKeyStore stub、Sandbox、HFS、IOSurface、HID 等兼容工作，`sweet-root-tweaks.py` 包含原机型使用的 Setup、迁移、watchdog 和无硬件服务调整。应按日志逐项复核兼容性，不能把一批守护进程不再报错等同于真实硬件已经实现。

现有激活状态相关调整也不等于获得 Apple 官方激活、Apple ID、App Store 或安全服务支持。SEP、Secure Enclave 数据保护、Face ID 和 Apple 硬件信任链未移植；已有测试内核放宽了安全策略，实验设备不适合保存个人敏感资料。

### 4.3 显示是桌面阶段的额外工作

已有补丁 `0082` 解决早期 framebuffer 映射，`0083` 为 **控制台** 接入 MTK 命令模式刷新。探针已证明继承 LK 显示状态可以出图；这不等于完整冷启动面板驱动已实现。

GUI 的 IOSurface/AppleCLCD 拷贝路径也必须在每次提交后触发 matisse 刷新。只让串口控制台调用刷新，切换 GUI 并关闭控制台后可能停留在旧画面。还要处理真实 1440×3200 面板与 iPhone X 1125×2436 逻辑画布之间的缩放、裁剪或留边，并让触摸使用完全相同的变换。

先用 CPU 绘制建立可见、可交互桌面；Mali GPU、Metal/OpenGL 兼容是独立的大项。原 sweet 的帧率和启动时间不能当作 K50 Pro 的实测性能。

## 5. 如何接通 UFS

### 当前状态

Android/Recovery 已能使用 UFS；**XNU 和 shim 在 matisse 上可靠读写 UFS 尚未证实**。当前 matisse ADT 不暴露 Qualcomm 的 `sweet-ufs` 节点，并强制 `ufsrw=0`。`ufsrw=1` 只是请求写权限，不能替代控制器移植。

live FDT 的主控制器是 `ufshci@112b0000`，compatible 包含 `mediatek,mt8183-ufshci`。兼容字符串复用旧型号并不表示板子是 MT8183。旧 shim 试验没有得到可靠的持久日志；控制器接管、时钟、电源、DMA/IOMMU 等仍需分别排查，不能把某一个猜测写成已找到根因。

### 建议实施顺序

1. **先收集只读基线。** 对照当前固件的 live FDT、Recovery 驱动和相应 MediaTek 厂商源码，记录电源域、clock/reset、PHY、HCE/HCS、UTRL 地址、运行状态、doorbell、错误状态、LUN 与逻辑块大小。避免访问尚未供电的 MMIO。
2. **确定接管方法。** 优先复用 LK 已建立的链路，但必须确认请求已静止、队列可停、UTRL 新地址写入可读回。只有完成这一点才允许提交自己的命令。失败后再研究完整 MTK 初始化：时钟、电源、PHY、UIC/link startup、设备初始化与链路电源模式，不能只改 MMIO 基址。
3. **先单队列轮询，只读命令。** 依次做 TEST UNIT READY、READ CAPACITY、读取已知块和 GPT。比较 Recovery 读取的相同 LBA 数据与 SHA256；验证 GPT CRC、LUN、分区范围和块大小。不要将 sweet 的 LUN0 或 4 KiB 假设直接当成本机事实。
4. **解决 DMA 与缓存。** 验证 CAP 对地址宽度的支持、UTRD/UCD/PRDT 对齐、物理地址高 32 位、连续内存、cache clean/invalidate 和屏障。上部 RAM 超过 4 GiB；若设备使用 IOVA/IOMMU，必须明确其地址转换关系，不能默认 CPU PA 就是总线地址。
5. **接入 XNU 块设备。** 复用 `sweet_ufs.c` 的设备接口、分区边界检查和文件系统衔接，把 MTK host 部分独立出来；先保持只读。分清设备物理块与 BSD 512-byte sector 接口。
6. **最后验证写入与根镜像。** 在明确批准的、备份充分的测试区域做限界写入、读回、flush 和恢复测试，再验证文件系统一致性与异常重启。只有达到这些标准，才考虑单独授权将 iOS 根写入既有 `userdata`；不改 GPT，不碰其他 LUN/引导区。

### 代码复核发现的待修问题

本次阅读 `shim/ufs.c` 还发现三个具体问题，应在复用该实现前修正并验证；它们不构成旧接管失败的唯一原因：

- `gpt_entries()` 用 `be32` 读取 GPT 的 entry LBA/entry count；GPT 使用小端，其中 entry LBA 是 64 位。
- SCSI 写命令的 Command UPIU 没有设置 W 数据方向标志；当前 `cmd[1]` 只处理读方向。
- 超时按固定 19,200,000 ticks 估算一秒，与本机 13 MHz 不符；应使用实际计时器频率。

这些问题位于 shim 的 UFS 实现，不能不经检查就推断 XNU 的独立 UFS 实现存在完全相同的问题。当前 `ufs_mark`、`ufs_log_write` 等都不能当作已验证的日志基础设施。

还有一个结构问题：现有 XNU 代码在 UFS 初始化成功后才调用触摸、按键、USB 和部分日志初始化。应把这些移到合适的平台/IOKit 启动阶段，使 RAM disk 启动也能单独测试它们。

## 6. 如何接通触摸

### 更正硬件身份

旧 NOTES 把 `fts@0` 写成 FocalTech，本次以真机绑定和日志更正：

| 项目 | 已读到的证据 |
| --- | --- |
| live compatible | `st,spi` |
| Recovery 驱动 | `spi6.0` 绑定 `fts`，加载 `fts_touch_spi.ko` |
| 固件名 | `st_fts_L11.ftb`，limits 文件名含 `stm_fts` |
| 总线 | SPI6，控制器 `0x11016000`，CS0，8 bits，mode 0，日志最大速率 12 MHz |
| GPIO | DT 中 IRQ GPIO8、reset GPIO3；GPIO编号不是 GIC INTID |
| 坐标 | 日志 Touch Resolution 为 14399×31999；DT 声明 14400×32000 超分辨率范围 |

证据支持按 **ST FTS** 驱动路线移植；具体芯片型号和协议版本仍应从芯片 ID、对应源码和现有固件确认，不能只靠 `fts` 名称推断。Recovery 日志能够读取固件版本和系统信息，同时出现固件文件加载 `-2`；这不等于芯片没有运行固件，也不代表必须立即刷写固件。

### 实施与验收

1. 从相匹配的厂商驱动提取 SPI 帧格式、dummy bytes、命令/事件 FIFO、reset 和供电时序。DT 记录的 `avdd/vdd` 和 MT6373 regulator 依赖需要核实，不能复用 Qualcomm RPMh。
2. 先保持当前可用固件与校准，完成 MTK SPI 最小收发；读芯片 ID/系统信息，连续重复并验证一致性，再启用触摸扫描。不要将自动固件升级、工厂测试或校准重写带入初版驱动。
3. 先轮询事件 FIFO，记录按下、移动、抬起和 contact ID；随后接入 GPIO/EINT 中断，处理清状态、排空 FIFO、超时与异常复位。
4. 把原始事件转为现有 HID digitizer 服务；复用 HID 上层接口，替换 `sweet_touch.c` 的 GENI/Goodix 底层。
5. 根据实际坐标范围做旋转、轴交换、缩放、裁剪/留边，再映射到 iOS 逻辑画布。不能把 14400 范围直接当作 1440 像素，也不能保留原 sweet 的 1080×2400 常量。
6. 验收四角、中心、连续拖动、抬起、多指、底部 Home 手势以及锁屏唤醒。能点击不代表手势完整，必须排查事件生命周期、边缘过滤与 HID 服务属性。

第一阶段无需触摸即可进入最小 PID 1；真实触摸是“iOS 可交互”阶段的要求。

## 7. 如何接通 USB

**Recovery 有 adb 和 LK 有 fastboot，只证明它们自己的 USB 栈可用；XNU 需要自己的 USB 驱动。** 这里优先让手机充当接到电脑上的 USB device。xHCI 的主机模式是另一条路径，不能用来直接代替 gadget/device 控制器。

本机为 MTU3/SSUSB，记录的资源包括 `0x11201000` 和 `0x11200000`。真正实现时要按 live FDT 的各 `reg`、`reg-names`、PHY、clock、power 和端口角色解释这些窗口，不能只拿两个基址照搬驱动。现有 `0072` 至 `0081` 中的 sweet USB 工作面向 DWC3，无法靠 `sweetusb=1` 在 MediaTek 上启用。

建议按以下顺序做：

| 阶段 | 工作 | 验收 |
| --- | --- | --- |
| U0：资源与角色 | 核实 MTU3 device 路径、PHY、时钟、电源、VBUS、Type-C/角色控制 | 控制器可读，device 角色稳定，无总线异常 |
| U1：最小枚举 | 完成 EP0、描述符、SET_ADDRESS、SET_CONFIGURATION、reset | 主机稳定识别正确描述符，多次插拔可恢复 |
| U2：调试通道 | 做 bulk IN/OUT 或合适的串口类；初期选择较简单的 FIFO/PIO 路径（若硬件支持），之后再加 DMA/QMU | 双向校验数据与连续内核日志，不丢包、不锁死 |
| U3：网络 | 移植 CDC ECM 类逻辑、内核网络接口和数据包路径 | 双向 ping；再验证 IP、路由、DNS、NAT 和较长传输 |
| U4：系统服务 | 根据需求增加文件传输、远程 shell 或相应用户态服务 | 服务长期稳定、断线重连正常 |

DWC3 的 TRB/endpoint 寄存器实现不能直接复用到 MTU3；USB 类协议与部分 ECM 网络逻辑才有复用价值。枚举、链路 up、ping 通和能上网是不同的验收点。

若使用 DMA/QMU，同样要处理 IOMMU/总线地址、缓存一致性、描述符对齐和中断。端口方向与 VBUS 还可能依赖 Type-C/充电芯片，不能照抄原 Qualcomm 驱动的 VBUS override。ADB、usbmux/lockdown 也不会在 USB 枚举后自动出现，它们需要另外的用户态协议实现。

USB 日志能显著减少后续对屏幕和断点回传的依赖；可以在正常 XNU 中断/线程运行稳定后优先做最小 USB 日志，再扩展 ECM 网络。

## 8. 推荐执行顺序与其他注意事项

| 优先级 | 下一项 | 完成标志 |
| --- | --- | --- |
| 1 | zone/object 及完整 VM bootstrap（已完成） | 实机到达完整 `vm_mem_bootstrap` 返回 |
| 2 | 正常异常向量、调度器、计时器/IRQ、内核线程（当前优先级最高） | 线程与定时中断持续运行 |
| 3 | RAM disk 最小用户态 | PID 1 多次心跳，系统调用可重复 |
| 4 | 稳定屏幕日志或 MTU3 最小 USB 日志 | 能连续观察真实内核故障，不依赖仅显示 logo |
| 5 | MTK UFS 只读、受控写入与根镜像 | 块数据校验、文件系统验证通过 |
| 6 | Apple launchd、dyld/cache 与核心服务 | 真实 iOS 用户空间持续运行 |
| 7 | GUI 帧提交刷新、ST 触摸和 MTK 按键 | 可见、可交互的 SpringBoard |
| 8 | USB 网络、电源/温控、多核及更多外设 | 每项独立测试后启用 |

以下是与本项目直接相关的约束：

- **设备恢复：** 每轮新 boot_a 写入单独确认。失败时先由用户进入 fastboot，写已验证的恢复前缀，进入 Recovery 后再写完整 64 MiB 并全量读回校验。只刷前缀曾遗留测试数据，不能算恢复完成。
- **日志与证据：** 优先同次启动日志。`adb exec-out dd` 曾静默短读，分区校验使用设备临时文件再 pull，并同时检查长度和 hash。直接 log_store、expdb 或 UFS 日志不是现成可靠通道。
- **内存：** 当前 XNU 配置的上部窗口是 `0x100000000..0x22e000000`，约 4.72 GiB；它不代表已经压力验证所有 RAM。后续内存扩展需逐个避开 secure/carve-out、framebuffer、回传及 DMA 保留区。
- **时钟和中断：** 使用本机 13 MHz、虚拟计时器 INTID27，避免复用 sweet 的 19.2 MHz/INTID19。GPIO编号、Linux IRQ 和 GIC INTID 不能混用。
- **多核：** 继续保持 `cpus=1`。原设备“六个 A55”方案不适用于 MT6983；以后按实际 MPIDR、A510/A710/X2 拓扑、PSCI、缓存与中断亲和性逐核启用。
- **电源与外设：** 按键走 MTK PMIC/SPMI；电池、充电、热管理、挂起恢复均未完成 XNU 移植。先短时可观测测试，再考虑长时间运行；不要把静态电量服务当作真实充电/温控。
- **后续大项：** Wi-Fi、蓝牙、基带、音频、摄像头、传感器、GPS、GPU、休眠唤醒都需要独立驱动/固件/用户态接口。桌面亮起不会自动补齐这些功能。
- **写入范围：** iOS 根的预定位置是现有 `userdata`，未来写入仍需当次明确确认；不修改 GPT，保留 bootloader、vendor_boot/Recovery、modem、persist 和校准备份。
- **构建管理：** 内核改动先单文件交叉编译，再通过 `gh` 使用 Actions 构建，记录运行 ID、提交和产物 hash。镜像、设备 dump、序列号、Apple 资源保留在忽略目录。完成的源码/文档更新提交，避免混入未验证的旧修改。

## 9. 本轮可核对的构建、恢复和交接信息

当前沿用 [GitHub Actions 39640336293](https://github.com/Jinsens/ios-sweet/actions/runs/39640336293)，内核构建提交 `dd9752e`。本轮没有新建内核 CI，也没有推送提交。

```text
kernel: xnu/out/ci-39640336293/kernel.development.sweet
SHA256: bc308789486ee96b380ce374bc7fe4c1894f4a115da9662b4cb0cf9a016b543c

本轮镜像: shim/out/entryprobe-el1-vm-return-brk.img
大小: 26,562,560 bytes
SHA256: 99d68ed6d5c870070a116e223b7ba29bd76b1e619c34e444f05146c92b1106fa

同次启动记录:
stage=87 fault=1 esr=f2005357 far=0 pc=fffffe00073644d8
x0=fffffe00080bbbc0（与主机模型预测一致；不是成功/失败返回码）

已验证的完整恢复备份:
backup/recovery-20261021/boot_a-live-20261021.img
大小: 67,108,864 bytes
SHA256: 2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1

证据目录（gitignored）:
research/matisse/20261022-xnu-resume/
  vm-return-brk-manifest.json
  vm-return-brk-dmesg.txt
  vm-return-brk-restored.img
  vm-return-brk-emulation.txt
  touch-driver-binding.txt
  vm-retire-boot-pages-disassembly.txt
```

关键提交：`f1b6d14` 为 TTBR0 恢复回传；`e24a92c` 为 x0 记录；`cfb5e8c` 为 CPU/GIC 实测记录；`490c41f` 为物理页初始化通过和完整恢复记录；`1e233f4` 为 zone/object 检查点准备；`3d638e1` 为完整 VM bootstrap 返回检查点准备。

本轮结束时没有尚未恢复的测试。完整 `vm_mem_bootstrap` 返回已在实机通过并完整恢复；主机模型同时确认真实写入物理地址最高 `0x1039e3fff`，128 MiB 清理范围足够，bootstrap 恒等映射在该范围内完整，而 `0x22e000000` 已不再映射，不能无证据扩大清理范围。

下一步不能直接带着当前诊断向量进入调度器：必须先把正常 XNU IRQ、SVC 和缺页异常入口恢复或做成只拦截指定 BRK 的选择性机制，并确认 DAIF 开放后中断持续投递。不要直接刷 iOS 根镜像。
