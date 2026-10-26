# Redmi K50 Pro（matisse / MT6983）触控移植诊断

更新：2026-10-27。代码提交：诊断 `b000e85`，后续供电试验 `998bf3b`（未上机）。
旧版报告中的供电结论已被本次 PMIF 寻址审计推翻；下文区分实测记录与尚未验证的解释。

## 实机 IC 与板级连接

Recovery 正常工作的触控驱动为 `fts_touch_spi`，输入设备为 `fts`。
通过原厂 `/proc/fts/driver_test` 发送硬件寄存器读取，取得：

| 项目 | 实测 |
|---|---|
| 数字 chip ID，`0x20000000` | `36 40` |
| FW，`0x20000004`，小端 | `0x0020` |
| System Info | 完整 208 字节，头部 `A5 01`，FW `0x0020`，cfg `0x001b` |
| ICR，`0x2000002d` | `0x42`，SPI4 位已置位 |
| Linux 输入坐标 | X `0..14399`，Y `0..31999`，10 个 slot |

这与 MiCode `rubens-s-oss` 的 `L11_fts_spi/fts_lib/ftsHardware.h` 中
**STMicroelectronics FTS / FTM5、SALIXP 分支**完全对应。
这是已确认的数字 ID 与协议分支；尚未取得芯片封装的商业料号，不能据此声称 ST80Y 或其他具体封装型号。

原厂 proc 请求（前导 `05` 是测试接口操作号）：

```text
05 FB 04 20 00 00 00 00 02 01
{ 000000003640 }
```

用户提供的 `Documents/dtbo.img` 含一个 overlay，触控节点与 live Recovery FDT 一致：

| 项目 | 板级值 |
|---|---|
| 节点 | `/spi6@11016000/fts@0`，`st,spi` |
| SPI | SPI6 CS0，mode 0，12 MHz，GPIO164–167 func2 |
| 复位 / 中断 | GPIO3 / GPIO8 |
| 固件文件 | `st_fts_L11.ftb` |
| VDD | MT6373 VRF18_AIF，1.8 V，consumer `spi6.0-vdd` |
| AVDD | MT6373 VTP，3.3 V，consumer `spi6.0-avdd` |
| PMIF | `0x1c015000`，SWINF2，SPMI SID5 |

Recovery `regulator_summary` 报告两轨 normal、各一个 consumer。
这是正常工作状态的参考，不能单凭 consumer 数量排除电气冲击。

Recovery 的寄存器参考：

| 地址 | 内容 | 值 |
|---|---|---|
| `0009` | MT6373 ID | `73` |
| `1bbf` | VRF18_AIF EN | `01` |
| `1d23` | VTP EN | `01` |
| `1e8d` | VRF18_AIF selector | `0c` |
| `1e35` | VTP selector | `0d` |

## 确定的软件错误：PMIF 使用了错误的命令类型

此前 XNU 的 `sweet_spmi.c` 将 `PMIF_CMD_REG=1` 用于上述 16 位地址。
SPMI 普通寄存器命令只支持 5 位地址；厂商 PMIF 的 16 位访问使用
`PMIF_CMD_EXT_REG_LONG=3`。厂商源码和 Recovery 实际 `spmi-mtk-pmif.ko`
反汇编一致：EXT_READL 基码 `0xc0000000`，EXT_WRITEL 基码 `0xe0000000`。
例如 SID5 地址 `0x1d23` 的单字节读命令应为 **`0xc5001d23`**。

因此，旧实验中的数字记录仍可保留，但不能继续当作目标 LDO 寄存器或电压的证明：

| 旧结论 | 本次更正 |
|---|---|
| 空写不崩溃，所以 PMIF 地址和写命令正确 | 不成立；没有证明实际访问了目标 16 位地址 |
| VRF18 已到 1.8 V、VTP 已到 3.3 V | 不成立；需要正确寻址后的读数与必要的实测 |
| selector 高半字节是模式位 | 未证实；旧读数不能用于推断目标寄存器位义 |
| 清 VTP LP 导致整机复位 | 只能保留“旧命令发出后整机复位”的观察，不能归因到目标 LP 位 |
| VTP 是唯一剩余硬件故障 | 未证实；应先重建可信的 PMIF 和 SPI 证据 |

**本次没有确定旧写命令实际落到哪个寄存器，也没有证明此错误已解释所有历史复位。**

历史上仍有用的记录包括 SPI 内部回环成功、波形采样变化、XNU ID 为零、部分命令后 watchdog 复位等。
内部回环只验证控制器内部通路，焊盘 DIN 采样也不能替代外部逻辑分析仪测量。
旧 `0x81` 单字节复位与 GPIO8 mux 的问题也会影响过去的解释。

## 移植修改

补丁依次为 `0106-matisse-touch-diagnostics-baseline.patch`、已有 `0107` / `0108`、
`0109-matisse-pmif-addressing-salixp-startup.patch`。
0106 补齐此前仅存在于 ignored 本地编译树的诊断前置代码，使 0108 可从补丁树重现；0109 给出最终修复状态。

- PMIF 读采用 type3，必须观察到 `WFVLDCLR`，读取数据并确认完成标志已清除才返回成功。
- `sweet_spmi_write()` 总是返回失败，不执行任何 MMIO；旧供电 ramp / LP / unlock 试验入口已移除。
- `sweetpmicaudit=1` 在挂载前读取上述五个 SID5 地址，连续八轮，不改变 PMIC 配置。
- `sweettouchinit=1` 仅在 ID 匹配后执行 SALIXP 启动：`FA 20 00 00 24 81` 软复位，
  最多等待 100 ms 的 `03` Controller Ready，读取 System Info 前 24 字节，
  校验 `A5 01` 和有效 FW，再发送 `A0 00 01` 开始扫描。该选项默认关闭。
- `87` 是本分支 FIFO 读取命令，不能当作“清 FIFO”；厂商 `flushFIFO()` 使用 `A4 00 01`。
- 控制器 Ready / error 事件会释放已有触点；修复 bitbang 短接收缓冲区的调试输出越界读取。
- 同步内核 mark 容量至 160，build ID 为 `0x230a`，解码器增加 201–204。

新 mark：

| code | 含义 |
|---|---|
| 201 | `a=pass<<32|error_bitmap`；`b` 从低字节起为 ID / EN8 / EN3 / SEL8 / SEL3；`a=ff` 表示初始化失败 |
| 202 | Controller Ready，`a=0/1`，`b=100` ms |
| 203 | `a=FW<<32|cfg`，`b=signature<<8|type` |
| 204 | Sense On 传输返回值与命令 |

## LK / VCP / DTBO 静态检查

原文件均未改动：

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `Documents/lk.img` | 2947648 | `32fa264a3e728d54ba204373c39bf339407dd9b6488165569a0762d2b5407c4a` |
| `Documents/vcp.img` | 1590800 | `8206c5b00a28dcd7206b88c019e5fd2a27a1ebca7f7d1c96d6631f00544ffea7` |
| `Documents/dtbo.img` | 75136 | `8bc2f0f11a09ecf36fc37f09111327b403566eb2372e77027d9b4d96da7a591c` |

LK 的 MTK 分段含 `lk` / `bl2_ext` / `aee` / `lk_main_dtb`。
`mt6373_ldo_1p8_enable` / `mt6373_ldo_3p0_enable` 字符串交叉引用附近访问
`0x1b87`、`0x1e29`、`0x1cce`，不是已确认的触控轨寄存器。
目前未找到可直接采用的 VTP 初始化路径。VCP header 为 `tinysys-vcp-RV55_A`；未运行、未刷写这些固件。

## 构建与镜像校验

本地完整 XNU 构建成功（CLT，`tools/build-xnu-clt.sh`，exit 0）。
七项定向测试通过（三项硬件 mock、四项 mark 解码）；mock 覆盖完整地址命令、完成所有权、禁止写 PMIC，以及 Ready / System Info 对 Sense On 的门控。

| 产物 | 字节数 | SHA256 |
|---|---:|---|
| kernel，build `0x230a` | 16723304 | `099fce838614cf3a9e0dc64f1b0492482b5ff5aef91cd8521b38d926565be6e9` |
| payload | 16719184 | `fe3a537cf859ea406769d142fa2d60fcbfcd4b0d6a557480796d61a555bc612c` |
| `matisse-xnu-230a-pmif-audit.img` | 7061504 | `20a5c23bdde95a90da47d57425a0703d26e53c897bd63618c48a56aa49dae412` |

Shim 使用 `49100674e2e889857a1e60554ffd8e095eeb5606` 的已提交源码在 ignored 目录构建，
工具链为 NDK 28.2 clang/lld。本轮保留用户已有的 shim `--kick` 未提交修改；该镜像未包含这些修改。
本地 XNU 还包含此前未提交的 `0093-sweet-log-readonly.patch`，其差异已记录在产物元数据中。
无新 GitHub Actions run。

已校验 boot header v4、完整 gzip、ARM64 Image 长度、SWEETAPP 对齐与长度、
payload 逐字节一致、stock ramdisk 1250233 B 和 signature 4096 B 完全一致。
镜像显式禁用 keys / battery / USB / touch-init，启用 touch ID probe 和 PMIC audit：

```text
debug=0xc serial=0 cpus=8 keepsyms=1 -enable_kprintf_spam rd=ufsroot0 ufsrw=0 ufscache=1 -noprogress sweetkeys=0 sweetbattery=0 sweetusb=0 sweettouch=1 sweettouchdelay=3 sweettouchhold=30 sweettouchmark=1 sweetpmicaudit=1 sweettouchinit=0
```

“PMIC 只读”指不写 PMIC 配置；启动仍会配置 SPI / GPIO，并使用既有 userdata 尾部诊断记录区。
测试使用临时 `boot_a` 镜像并恢复完整 64 MiB 备份，不改变 GPT。

## 本轮上机状态

用户已于 2026-10-27 明确回复“可以启动测试”。本轮产生 57 个新 mark，build ID `0x230a`。
8 轮 mark201 全部相同且错误位为零：

| 寄存器 | XNU 实测 | Recovery 参考 |
|---|---|---|
| MT6373 ID | `73` | `73` |
| VRF18 EN | `00` | `01` |
| VTP EN | `00` | `01` |
| VRF18 selector | `0c` | `0c` |
| VTP selector | `04` | `0d` |

这建立了正确寻址后稳定的参考：本次 XNU 启动时两轨的使能位都为零。
VTP selector `04` 对应厂商表 1.8 V；使能位为零时不能据此声称引脚实际输出 1.8 V。
mark96 的 SPI 返回码为 0、ID 为 `00 00`；触控线程已于 3 s 运行，30 s 等待窗口完整结束。
此后 mark63/64 显示 init 候选路径返回 ENOENT，与已知缺少 launchd 的阶段一致。

约 135.47 s 后进入 fastboot，经 fbraw 恢复完整 64 MiB `boot_a` 备份并返回 Recovery。
恢复后 SHA256：`2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`。
Recovery 报告 bootreason Watchdog；本次没有 PMIC 写，不能归因到电源切换。
未取得 SWEETLOG console header；以新 build 的持久 mark 为证据。
原始结果为 `audit-result.json`、`audit-after-marks.bin` / `.txt`。

**XNU 触控尚未工作。** 下一步应使用正确 type3 写命令完成有边界的 LDO 配置，
验证读回后，再启用 SALIXP 初始化。该后续镜像尚未上机。

## 后续镜像 0x230b：已构建，未上机

补丁 `0110-matisse-verified-touch-supplies.patch` 增加单独的
`sweet_spmi_touch_configure()` API；旧通用写接口继续禁止写入。
新 API 固定 SID5，仅允许 VRF18/VTP selector 的已知目标及 EN bit0，
以 type3 写入，保留其他位，每笔写后核对读回；若轨已开启且需改变 selector 则拒绝。

`sweettouchrails=1` 的流程：先保持复位为低，检查与本次实测完全一致的
`73/00/00/0c/04` 初始快照；在两轨关闭时设置两个 selector；开启 VDD，等 5 ms；
开启 AVDD，等 10 ms；再检查 `73/01/01/0c/0d`。全部通过才释放复位，
进入 ID 检查和 SALIXP 初始化。失败时不释放复位、不启动 SPI 轮询。
这是为已测状态准备的诊断流程，尚未证明实机供电和触控能正常启动。

新增 mark205 初始状态、206 每步配置返回值/读回、207 供电配置后状态、208 复位释放。
两项新增 mock 测试验证写操作边界、状态/读回失败时阻止复位释放；合计 9 项定向测试通过。
完整本地 CLT 构建 exit0，shim 使用已提交 `b000e85` 的冻结源码与 NDK28.2。

| 产物 | 字节数 | SHA256 |
|---|---:|---|
| kernel，build `0x230b` | 16723464 | `8b3a9cdda5f7aa7bf3b936ffeea826b22e18c6a14156cd78ea88b7e033b56c76` |
| payload | 16719344 | `b52f381cb1383c9b35bdb20beb03c65bf8976fcf7236e9cae2b00fa4f1823e2f` |
| `matisse-xnu-230b-touch-power.img` | 7065600 | `a9e52c55d08c6d5896694942eb39a36ff5f7f7f7481d152eae229f55d47f8336` |

已通过与上一镜像相同的 v4 / ramdisk / signature / gzip / payload 校验。
产物、元数据与默认只读的 `run-power.py` 在研究目录的 `power-230b/` 子目录。
参数在上一轮基础上改为 `sweettouchinit=1` 并添加 `sweettouchrails=1`，其他诊断开关一致。

此镜像将实际开启触控两路 LDO。此前“可以启动测试”已用于 `0x230a`；
依据 AGENTS 逐次确认要求，新的 `boot_a` 测试尚待用户确认。执行后仍须恢复完整备份并校验。

## 证据位置与下一步

原始实测、固件反汇编、厂商源码、构建日志、镜像、校验 JSON 和运行脚本都在 ignored：
`research/matisse/20261027-ic-diagnosis/`。

厂商参考版本：MiCode Xiaomi_Kernel_OpenSource，`rubens-s-oss`，commit
`270b84910b941bf75490fccdb5fb2b3021dd7cbf`。
关键路径是 `drivers/spmi/spmi-mtk-pmif-core.c`、
`drivers/input/touchscreen/L11_fts_spi/fts_lib/{ftsHardware.h,ftsCore.c,ftsTool.c}`。

下一步是经确认启动 `0x230b`，按 mark205–208 检查 LDO 配置，再读取 mark96 的 ID 和 mark202–204 的初始化结果；只有实际事件上报到 HID 后，才能确认触控移植工作。
