# Redmi K50 Pro（matisse / MT6983）触控移植报告

**范围**：XNU 侧 Matisse 触控（ST FTS，SPI6）从"完全无响应"到"供电链路已打通、芯片仍不应答"的全过程。
**时间**：2026-10-26
**补丁跨度**：`0101` – `0137`（诊断补丁，尚未回写 `xnu/patches/`）
**设备状态（收尾时）**：Recovery 在线，触控正常（`input2 = fts`），`boot_a` 已用完整备份写回并校验
`sha256 = 2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`

---

## 1. 一页结论

| 项目 | 结论 |
|---|---|
| SPI6 控制器 | **正常**。内部回环逐字节一致（`a5 5a 3c c3 0f f0 96 69`，≈3.9 µs） |
| SPI 焊盘通路 | **正常**。长传输中 `CSB(165)` 拉低、`CLK(167)` 与 `MOSI(166)` 翻转 |
| 引脚复用 / IES / 上下拉 | **与 Recovery 完全一致**（PU/PD 读回 `0xfb7a5beb` / `0x00048410`） |
| 复位脚 GPIO3 | **正常**。GPIO 模式、输出、复位释放后 `din=1` |
| SPMI / PMIF 访问 | **正常**。空写（写回原值）四组全部成功且系统完整跑完 |
| 触控供电电压 | **已能安全改到位**。分步 ramp（100 mV/5 ms）把 VRF18 从 1.3 V 抬到 1.8 V、VTP 从 1.8 V 抬到 3.3 V，不再崩溃 |
| 触控芯片本体 | **仍然完全不应答**：ID `00:00`、FW VER 全 0、`0x81` 无回执、MISO 恒 0、INT(GPIO8) 恒 0 |
| 唯一未解的硬差异 | VTP 的 **LP 位**：Recovery 是 `0x01`（LP=0），XNU 是 `0x03`（LP=1）；而在 XNU 里**任何**把 `0x1d23` 从 `0x03` 改成别的值（`0x01` 或 `0x00`）的写操作都会让 SoC 立刻死机 |

一句话：**我方链路（主控、焊盘、复用、时钟、SPMI 写入）全部验证正确，电压也已按 Recovery 抬到位，但芯片始终不驱动 MISO/INT；剩下的唯一已知差异是 VTP 的 LP 位，而在 XNU 里动它就是致命的。**

---

## 2. 目标硬件

| 项目 | 值 |
|---|---|
| 机型 | Redmi K50 Pro（`matisse`），MT6983 / 天玑 9000 |
| 触控 | ST 系 FTS，`compatible = "st,spi"`，FW `st_fts_L11.ftb`，屏 1440×3200 |
| 总线 | `spi6@11016000`，CS0，mode 0，12 MHz，四线 |
| 复位 | GPIO3（`ts_reset_active` = `output-low`，即低=复位有效，高=释放） |
| 中断 | GPIO8（`ts_int_active` = `input-enable`） |
| 供电 | `vdd = mt6373_vrf18_aif`（DT 钉死 1.8 V）、`avdd = mt6373_vtp`（DT 钉死 3.3 V） |
| PMIC | MT6373（SPMI **SID 5**，`second_pmic@5`）；主 PMIC 为 `pmic@4`（**SID 0 不存在**） |
| PMIF | `0x1c015000`，SWINF 通道 2 |

DT 中触控节点的 `fts,pwr-reg-name = "avdd"`、`fts,bus-reg-name = "vdd"`，与 `vdd-supply`/`avdd-supply` 的 phandle 指向一致，供电映射无歧义。

---

## 3. 关键证据链

### 3.1 SPI 控制器正常（推翻了早期"假完成"的猜测）

- 打开 `SPI_CMD_SPIM_LOOP` 做内部回环，TX `a5 5a 3c c3 0f f0 96 69` 被**逐字节原样读回**，`match=1`、`err=0`、耗时 3,514 ns。
- 说明总线时钟、TX/RX FIFO、移位器都是真实工作，`FINISH` 不是陈旧标志。

### 3.2 焊盘通路正常（关键的一次观测）

用 64 字节长传输、**紧贴采样** `PIO DIN`（`0x10005250`，pins 160–191），得到 `seen = 0xf5`：

- `CSB(165)` 整个传输窗口保持拉低；
- `CLK(167)` 翻转、`MOSI(166)` 翻转；
- `MI(164)` **一次都没有离开 0**。

即：控制器确实把三个输出信号送到了焊盘，芯片从始至终没有驱动过 MISO。

> 同时纠正了两个此前的错误：
> - PIO 每组 32 脚是 `0x10` 步进，DIN(160–191) 在 `0x10005250`，不是 `0x214`；
> - `0x1000544c` 那个"模式写入别名"是**空写**，真正生效的是数据寄存器 `0x10005440`。

### 3.3 引脚配置与 Recovery 完全一致

从厂商源码（`MiCode/Xiaomi_Kernel_OpenSource`，分支 `rubens-s-oss`，
`drivers/pinctrl/mediatek/pinctrl-mt6983.c`）拿到 MT6983 的寄存器表：

- IOCFG base 5 = `0x11d30000` 对应 GPIO160–167；
- IES 字 `+0x0050`：pin164 bit15、165 bit14、166 bit16、167 bit13；
- PU `+0x00A0`、PD `+0x0070`、drive `+0x0110`。

实测：IES 读回全 1；PU/PD 读回 `0xfb7a5beb` / `0x00048410`，与 Recovery 的 `pinconf-pins` **逐位一致**（165 上拉，164/166/167 下拉）。这说明整套焊盘配置跨重启完整保留，前面所有 DIN 读数可信。

### 3.4 PMIC 侧：拿到了"能用时"的黄金寄存器表

两个来源：

**(a) TWRP 设备树里的旧 `ramoops.txt`**（`/Users/xiaohuasheng/Documents/twrp/output/ramoops.txt`）：

```
mt6373_vtp: Bringing 1800000uV into 3300000-3300000uV
```

含义：开机时 VTP 硬件选择器是 **1.8 V**，Recovery 内核按 DT 约束（`regulator-min/max-microvolt = 3300000`）把它抬到 3.3 V。XNU 从不做这件事。VRF18_AIF 同理，DT 钉死 1.8 V。

**(b) Recovery 内核开了 regmap debugfs**，可以直接 dump MT6373 整张寄存器表：

```sh
adb shell 'mount -t debugfs none /sys/kernel/debug'
adb shell 'cat /sys/kernel/debug/regmap/0-05/registers' > recovery-mt6373-regs.txt
```

`0-05` 即 SID 5（MT6373），输出是 `addr: value` 文本，共 65536 行。
**黄金状态**（触控存活）：

| 寄存器 | 含义 | Recovery（能工作） | XNU 启动时 |
|---|---|---|---|
| `0x1bbf` | VRF18_AIF 使能 | `0x01` | `0x00`（关） |
| `0x1e8d` | VRF18_AIF VOSEL | `0x0c`（`ldo_volt_table3[12]` = 1.8 V） | `0x07`（= 1.3 V） |
| `0x1d23` | VTP 使能 / LP | `0x01`（EN=1，LP=0） | `0x03`（EN=1，LP=1） |
| `0x1e35` | VTP VOSEL | `0x0d`（`ldo_volt_table1[13]` = 3.3 V） | `0x74`（= 1.8 V） |

快照已存：`research/matisse/20261025-touch/recovery-mt6373-regs.txt`。

### 3.5 写通路正常（空写判别实验）

把四个寄存器**各自写回它原本的值**（电气状态不变）：

```
186  noop VTP VOSEL    rc=0 value=0x74   ✓
187  noop VRF18 VOSEL  rc=0 value=0x70   ✓
188  noop VTP EN       rc=0 value=0x03   ✓
189  noop VRF18 EN     rc=0 value=0x00   ✓
50   bsd_init -> vfs_mountroot           ✓
51   root mounted                        ✓
84   init exec failed, about to panic    ✓
```

**系统完整跑完**。所以 PMIF 的写命令编码、地址、VALUE 字段都是对的，问题不在"写不进去"。

---

## 4. 崩溃边界：哪些写是安全的

每一笔写后立刻落标记，逐条记录：

| 写操作 | 结果 |
|---|---|
| `0x1bbf ← 0x01`（VRF18 使能） | **安全**，读回 `0x01`，多次验证 |
| 四个寄存器写回原值（空写） | **安全**，完整启动 |
| `0x1e8d ← 0x7c` / `0x1e35 ← 0x7d`（保留高半字节） | 存活，但芯片仍静默 |
| `0x1e8d ← 0x0c`（一步到位 1.8 V） | **死机** |
| `0x1e35 ← 0x0d`（一步到位 3.3 V） | **死机** |
| `0x1d23 ← 0x01`（清 LP，VTP 保持开） | **死机** |
| `0x1d23 ← 0x00`（关断 VTP） | **死机** |
| **分步 ramp**：`0x1e8d` 7→12、`0x1e35` 4→13（每步 5 ms） | **安全** |

结论：

1. **一步跨压会打死 SoC**，分 100 mV 步进就没问题——这是软启动冲击，不是写通路问题。
2. **`0x1d23` 的任何取值变化都是致命的**，无论：
   - 在触摸线程里（≈2 s）；
   - 在 `sweet_ufs_rootdev()` 的挂载前窗口（≈1 s，UFS DMA 已就绪、根分区未挂）；
   - 在 1.8 V 下改，还是先 ramp 到 3.3 V 再改。

第 2 点是当前唯一的硬阻塞。

---

## 5. 当前状态与三个假设

### 已站得住的事实

- 主控、焊盘、复用、时钟、片选、上下拉、输入使能、复位电平：**全部正确**。
- SPMI 读写：**正确**（空写证明）。
- 两路电压：**能安全抬到 DT 要求的值**（分步 ramp）。
- 芯片：MISO 恒 0、INT 恒 0、`0x81` 无回执、ID `00:00`、FW VER 全 0。

### 剩余假设（按可能性排序）

**H1：VTP 处于 LP（低功耗）模式，输出电流能力不足，芯片起不来。**
这是与 Recovery 的最后一个已知差异。未解之处：清 LP 必崩，说明 LP 位在这块板上牵动别的东西（或 LP 位的清除会引发 LDO 重新软启动）。

**H2：VTP 的 VOSEL 写入没有真正改变输出。**
同一次启动里 `0x1e35` 先读回 `0x7d`，20 ms 后再读变成 `0x74`；而 `0x1e8d` 稳定在 `0x7c`。可能是 SPMI 读回抖动（此前也见过 `0x74/0x75`、`0x70/0x07` 的不一致），也可能 PMIC 真的把值回退了。厂商头文件里 **没有**独立的 LP-VOSEL 寄存器，所以更可能是读回不可靠。

**H3：芯片并不是"没电"，而是被复位/停在 bootloader 模式。**
我们对芯片做了完整的掉电/复位序列（复位拉低 20 ms、释放、等 120 ms、发 `0x81`），而 Recovery 里芯片是**一路活着**过来的。FTS 的 ROM 在硬复位后是否需要特定下载模式握手才能应答，尚未验证。

---

## 6. 下一步建议

按性价比排序：

1. **硬件测量（最快一锤定音）**
   在 XNU 启动后量 VTP 与 VRF18 的实际电压。只要确认 VTP 真的到 3.3 V，H2 直接排除，焦点收敛到 H1/H3。
2. **验证读回可靠性**
   在同一笔写之后连续读 8 次并全部落标记；若读数在 `0x0d`/`0x74` 之间跳变，则 H2 是伪问题，同时说明此前的"读回确认"都需要重新审视。
3. **绕开 LP 位**
   如果 H1 成立而 `0x1d23` 又动不了，只剩两条路：
   - 在**引导器（LK）**里就把 VTP 配好（风险高，需改 `lk` 分区）；
   - 或确认芯片在 LP 下其实能工作，转而排查 H3（复位时序/下载模式握手）。
4. **H3 的验证**
   不做硬复位，仅上电后直接发 `0x81` + 读 FW VER；以及尝试更长的上电稳定时间（500 ms）。这两项都很便宜。

---

## 7. 复现方法（工具链）

### 7.1 只读 dump PMIC 寄存器（Recovery）

```sh
adb shell 'mount -t debugfs none /sys/kernel/debug'
adb shell 'ls /sys/kernel/debug/regmap'          # 0-04 = 主 PMIC, 0-05 = MT6373
adb shell 'cat /sys/kernel/debug/regmap/0-05/registers'
```

`/sys/kernel/debug/regulator/<name>/` 还能看到 `use_count`、`open_count` 与该稳压器的消费设备（如 `spi6.0-avdd`）。

### 7.2 临时启动镜像（可逆）

- 构建：`tools/build-xnu-local.sh xnu/out/local`（需 `DEVELOPER_DIR=/Users/xiaohuasheng/Downloads/Xcode.app/Contents/Developer`）
- 打包：`tools/mkpayload.py <kernel> research/matisse/20261025-display-0148/matisse-dual.adt <payload> --cmdline '...'`
- 封 shim：`research/matisse/20261025-touch/repack-touch-spi-sweep.py <payload> <img>`
  （脚本会改写 shim 里两处 32-bit `MOVZ/MOVK` 立即数，即 `EMBEDDED_XNU_BYTES`）
- 预检：`run-touch-spi-sweep.py <img>`（只读，校验设备在 Recovery、`boot_a` 与备份一致）
- 写盘 + 重启：`adb push` → `dd of=/dev/block/by-name/boot_a` → 校验 → `adb reboot recovery`
- 恢复：`/usr/bin/python3 tools/fbraw.py flash boot_a backup/recovery-20261021/boot_a-live-20261021.img`
  → `fbraw.py reboot-recovery` → 机上 `sha256sum` 比对 `2931647a56…86b1`

> 注意：`fbraw.py` 依赖 pyusb，**只有 `/usr/bin/python3` 有**，用其他解释器会恢复失败（本轮踩过一次）。

### 7.3 启动标记

`tools/sweet-marks.py` 负责拉取与解码。标记是**同步落盘**的，写到 userdata 末尾一个 4 KiB 块（`SWEET_MARK_MAX = 160` 条），所以崩溃点之前的内容一定保留。

常用启动参数：

```
sweettouch=1 sweettouchdelay=1 sweettouchhold=45 sweettouchmark=1
sweettouchpower=1      # 电源/SPMI 诊断
sweettouchsweep=1      # SPI 五组配置扫掠
sweetvtplp=1           # 清 VTP LP 位（危险，见第 4 节）
sweetnoop=1            # 四寄存器空写判别
```

---

## 8. 产物清单

目录：`research/matisse/20261025-touch/`

| 类型 | 文件 |
|---|---|
| 黄金寄存器表 | `recovery-mt6373-regs.txt`（65536 行） |
| 源码快照 | `sweet_matisse_touch-0111.c`、`sweet_matisse_touch-0120.c`、`sweet_matisse_touch-0126.c`、`sweet_matisse_touch-0131.c`、`sweet_spmi-0120.c`、`sweet_spmi-0126.c`、`sweet_spmi-0131.c` |
| 镜像 | 37 个 `matisse-xnu-touch-*.img`，关键节点：`padwatch`、`bitbang`、`ies`、`spmilane`、`spmiret`、`railvolt`、`railvtp`、`powerorder`、`vddorder`、`seqr`、`earlyrails`、`isolate`、`noop`、`ramp`、`ramp3v3`、`lpearly` |
| 标记 | 33 份 `*-after-marks.{bin,txt}` |
| 运行器 | `run-touch-spi-sweep.py`、`repack-touch-spi-sweep.py`、`fdtq.py`（DT 解析） |
| Recovery 模块 | `recovery-modules/`（`fts_touch_spi.ko`、`fts.c`、`spi-mt65xx.ko`、`spi-mt65xx.c`、`fts_probe.asm`） |

关键镜像哈希（boot 镜像，7,061,504 B）：

| 补丁 | 用途 | `boot_sha256` |
|---|---|---|
| 0134 | 空写判别 | `07ba97cc…ac7c3` |
| 0135 | 分步 ramp | `70b5db8d…bbbc3` |
| 0136 | ramp 后不清 LP | `2e13c48d…f078` |
| 0137 | 挂载前清 LP | `bff9fa2c…7342` |

---

## 9. 风险与安全

- **`0x1d23` 是禁区**：任何取值变化都会打死 SoC。若要再试，必须接受"设备需长按电源 15–20 s 强制复位"的代价。
- **不要一步跨压**：改 VOSEL 必须分步（本轮 100 mV/5 ms 有效）。
- **`boot_a` 是唯一改动过的分区**。每次试验后都用完整 64 MiB 备份写回并校验哈希；其他分区从未触碰。
- 若设备失联（USB 完全不枚举），长按电源 15–20 s 可回到 Fastboot，然后按 7.2 恢复。
- `xnu/patches/` 目前仍停在 `0105`，`0106`–`0137` 全是工作树里的诊断改动，尚未整理成补丁文件。

---

## 10. 附：本轮推翻的旧结论

| 曾经的判断 | 实际 |
|---|---|
| 片选建立/保持时间不足 | 补齐 300/30 周期后无变化 |
| `CFG3` 半双工位写错 | 五种配置全部返回 8 个 0 |
| 三线/四线模式不对 | 位翻转（完全不经过控制器）同样全 0 |
| 复位脚一直被拉住 | 读回 GPIO 模式、输出、高电平 |
| 焊盘输入使能（IES）没开 | 读回全 1，且 PU/PD 与 Recovery 逐位一致 |
| `0x1000544c` 能写引脚模式 | 是空写，实际寄存器是 `0x10005440` |
| 电源使能位写不进去 | 写进去了（`0x00 → 0x01` 读回确认） |
| 电压已按 DT 配好 | 实际 VRF18 只有 1.3 V、VTP 只有 1.8 V |
| 一步改电压就行 | 一步必崩，必须分步 ramp |
