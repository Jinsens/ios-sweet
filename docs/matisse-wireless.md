# matisse 无线局域网与蓝牙移植交接

2026-11-13。当前完成硬件清点、AP 侧只读诊断入口和蓝牙协议基础；
尚未证明无线子系统上电、固件启动、扫描、联网或配对。仓库名仍为
`ios-sweet`，本次实机目标是 **matisse / MT6983**，不能按旧 SM7150
或 MT6768 的寄存器、固件和传输协议启动。

## 已核实硬件

证据为 Recovery 现场 FDT，SHA256
`a8ce3472ca39dfc128c8ad3f83c100806ba71578f7f275c422cda72c5f2df71b`。
原始 FDT、报告和固件只放忽略目录
`research/matisse/20261113-wireless/`，不提交设备转储。
本次清点没有打开无线字符设备、加载模块或读写无线 MMIO。

| 项目 | 现场声明 |
| --- | --- |
| Wi-Fi | `mediatek,wifi`，AP MMIO `0x18000000 + 0x700000` |
| Wi-Fi 中断 | SPI 554 / 569，即 GIC INTID 586 / 601 |
| BT | `mediatek,bt`，BGF / Conninfra 内部控制器 |
| BT 中断 | SPI 555 / 570 / 556，即 INTID 587 / 602 / 588 |
| BTIF | `0x1100c000 + 0x100`；APDMA TX `0x11301400 + 0x80`、RX `0x11301480 + 0x80` |
| BTIF 中断 | SPI 234 / 226 / 227，即 INTID 266 / 258 / 259 |
| BTIF 时钟 | `btifc`、`apdmac`，提供者 `syscon@11036000`，索引 10 / 33 |
| 固件 flavor | Wi-Fi 和 BT 的 `flavor_bin` 都是字符串 `_t` |

现场保留区必须在 XNU 的内存分配及 DMA 映射计划中排除，不能因为
Android 声明了 `no-map` 就假定 XNU 已采用相同边界：

| 用途 | 地址与大小 | 半开区间 |
| --- | --- | --- |
| Conninfra 总保留区 | `0x8f800000 + 0x800000`，8 MiB | `[0x8f800000, 0x90000000)` |
| Conninfra `emi-addr/size` | `0x8f800000 + 0x7e0000` | `[0x8f800000, 0x8ffe0000)` |
| SCP 共享区 | `0x8ffe0000 + 0x20000` | `[0x8ffe0000, 0x90000000)` |
| Wi-Fi 专用 DMA 池 | `0xbe000000 + 0x1400000`，20 MiB | `[0xbe000000, 0xbf400000)` |
| GPS EMI（相邻但独立） | `0x7fc00000 + 0x100000` | `[0x7fc00000, 0x7fd00000)` |

Wi-Fi 的 `memory-region` 实际指向上述 20 MiB DMA 池；Wi-Fi 自身
`emi-addr=0` 并不表示物理地址为零，也不能当作一块空闲内存。

## 原厂参考与地址空间

Conninfra 和 BT 参考固定为
[MiCode/MTK_kernel_modules `c66b3c9`](https://github.com/MiCode/MTK_kernel_modules/tree/c66b3c9ce7b5c0bc3d72a829f6542200deaa8a9c)，
分支 `rubens-s-oss`。主要依据：

- [MT6983 上电序列](https://github.com/MiCode/MTK_kernel_modules/blob/c66b3c9ce7b5c0bc3d72a829f6542200deaa8a9c/connectivity/conninfra/platform/mt6983/mt6983_pos_gen.c)
  和 [可读性检查](https://github.com/MiCode/MTK_kernel_modules/blob/c66b3c9ce7b5c0bc3d72a829f6542200deaa8a9c/connectivity/conninfra/platform/mt6983/mt6983_consys_reg.c)。
- [BT MT6983 平台](https://github.com/MiCode/MTK_kernel_modules/blob/c66b3c9ce7b5c0bc3d72a829f6542200deaa8a9c/connectivity/bt/mt66xx/include/btif/platform_mt6983.h)、
  [固件与 WMT](https://github.com/MiCode/MTK_kernel_modules/blob/c66b3c9ce7b5c0bc3d72a829f6542200deaa8a9c/connectivity/bt/mt66xx/btif/btmtk_mt66xx.c)、
  [H4 接收](https://github.com/MiCode/MTK_kernel_modules/blob/c66b3c9ce7b5c0bc3d72a829f6542200deaa8a9c/connectivity/bt/mt66xx/btmtk_main.c)。

MiCode 此分支没有完整 SOC7 WLAN 实现；补充参考固定为
[OnePlus MT6983 `a362bee`](https://github.com/OnePlusOSS/android_kernel_modules_oneplus_mt6983/tree/a362bee38655f234d8ba4d5e24ccaa465254aef1)，
主要为 [SOC7 驱动](https://github.com/OnePlusOSS/android_kernel_modules_oneplus_mt6983/blob/a362bee38655f234d8ba4d5e24ccaa465254aef1/vendor/mediatek/kernel_modules/connectivity/wlan/core/gen4m/chips/soc7_0/soc7_0.c)
及其 `include/chips/soc7_0.h`。这能证明该平台代码的寄存器和流程，
不能证明其板级 RF / FEM / NVRAM 参数适用于 matisse。
参考文件 SHA256 分别记录于 `xiaomi-selected-sources.json`、
`oneplus-selected-sources.json` 和 `bt-reference/source-manifest.json`。

WLAN 属于 **SOC7 / Connac 2.x，AXI HIF / WFDMA**。SOC7 源码的
`SOC7_0_CHIP_ID=0x7961` 是代码选择常量，不是本次实机读取的芯片 ID。
必须区分 WLAN 内部 chip address、AXI 窗口偏移和 AP 物理地址：

| 内部地址 | `_HIF_AXI` 窗口偏移 | 以现场基址换算的 AP 地址 |
| --- | --- | --- |
| `0x54000000`，WFDMA DMA0 | `0x402000` | `0x18402000` |
| `0x55000000`，WFDMA DMA1 | `0x403000` | `0x18403000` |
| `0x830c0000`，MCU bus/remap | `0x400000` | `0x18400000` |
| `0x80020000`，WF top off | `0x4b0000` | `0x184b0000` |
| `0x88000000`，MCU cfg | `0x4f0000` | `0x184f0000` |
| `0x7c000000`，Conninfra | `0x000000` | `0x18000000` |

这些换算来自原厂映射表，尚未用实机读值验证。不可将内部地址直接
传给 `ml_io_map`。参考 AP command TX ring 15、固件下载 TX ring 16；
RX event ring 2，收发描述符和 DMA cache 一致性仍需实际移植。

[hakios-mt6768 `84b0252`](https://github.com/oosexgang/hakios-mt6768/tree/84b025233e7350f3549ec5b8eb330fd74b4602f4)
可参考 iOS 网络接口和设置集成，但其 MT6631、旧 PDMA、1 MiB
Wi-Fi 窗口、STP/WMT 和电源流程不适合直接套到 MT6983。

## 蓝牙协议与真实固件

本平台 `ENABLESTP=FALSE`，BTIF 上是原始 **H4**，没有 STP 包头。
RX 指示字节支持 ACL `02`、SCO `03`、事件 `04`、ISO `05`；ISO
长度取 14 位。WMT FUNC_CTRL 的完整 H4 开启命令为
`01 6f fc 06 01 06 02 00 00 01`，关闭时末字节为 `00`。
对应事件必须精确为 `04 e4 05 02 06 01 00 status`，`status=0`
才表示控制器返回成功；当前没有实机收到过此事件。

MCU 和 BT 固件首先复制到共享 EMI，再解除 BGF MCU reset，不是经
普通串口发送整份固件。外层 EMI 头 48 字节，随后仍包含内层 48 字节
MCU/BT 头。原厂取地址字段的中间两字节作偏移。
离线 `vendor_a.img` 提取的当前 MCU 偏移为 `0`，当前 BT 偏移为
`0x5bc00`。旧源码中已注释掉的 BT `0x100000` 窗口与真实文件不符；
本移植要求调用者明确给出每份镜像的窗口并检查重叠和保留区边界。

校验算法有原厂历史行为：`int32_t` 累加后在符号位出现时，使用
算术右移折叠高 16 位。普通无符号 Internet checksum 在当前 BT
文件上相差 2。`mtk_bt_firmware.c` 用确定的无符号环绕及显式符号
扩展复现文件格式，避免 C 的有符号溢出未定义行为。
十份 MCU/BT 实际文件的校验和全部匹配；九份严格解析通过。
旧 `soc7_0_ram_bt_1a_1_hdr.bin` 为 2,927,044 字节，地址低字节
`0x11` 会被原厂代码丢弃，当前解析器以不支持的地址形式拒绝它。
其校验和匹配也不代表可以加载。结果见忽略目录
`bt-firmware-audit.txt`，全部文件大小和哈希见 `firmware-manifest.json`。

**尚未选择加载固件。** 现场 `_t` 与此旧参考只取 flavor 首字符的
代码不完全一致，还需核对设备正在使用的驱动版本、ECO 和完整的
命名逻辑。不得通过文件名相近、日期较新或校验成功就自动选定。

## 本次代码

- `tools/matisse-wireless-inventory.py`：读取 FDT、模块和驱动归属，
  解析 phandle、IRQ 和保留区；不启动控制器。
- `xnu/wireless/mt6983_conninfra.{c,h}`：只读 AP 电源及总线保护状态，
  固定访问 `0x1c001e04`、`0x10001c9c`、`0x10001c5c`。
- `xnu/wireless/sweet_wireless.c`：显式 `sweetconn=1` 且 ADT 同时声明
  `xiaomi,matisse` / `mediatek,mt6983` 才读上述三处；释放映射；
  不映射 Conninfra off-domain、不修改电源、不加载固件。
- `xnu/wireless/mtk_bt_h4.{c,h}`：调用者提供缓冲区的 H4 重组和
  严格 WMT 编解码。错误锁定，必须先重置/排空底层传输再 reset；
  不在错误载荷中搜索新帧。
- `xnu/wireless/mtk_bt_firmware.{c,h}`：纯内存 EMI 头及校验验证，
  不分配 DMA、不映射物理地址、不复制到设备。
- `xnu/patches/0217-matisse-wireless-foundation.patch`：构建和平台入口
  接入；规范源码保存在 `xnu/wireless/`。

AP gates open 只能说明这三个 AP 寄存器的快照符合条件；不能说明
Conninfra off-domain 时钟或固件有效。原厂可读性检查需要重置时钟
检测器再轮询；该只读快照故意不替代它。蓝牙解析器 7 项实际 C
ASan/UBSan 测试通过，覆盖分片、合包、截断、错误锁定、ISO、WMT、
超长帧、校验跨符号位、地址和窗口溢出；两个 C 文件通过 iOS16
arm64 单文件交叉编译。完整内核构建、总测试数和实机结果以
`NOTES.md` 最新交接为准；本节不声称已启动无线硬件。

## 下一步与验收点

1. 冻结当前内核/display/GPU 基线，验证无线保留区没有被 XNU 管理；
   完成具体只读诊断镜像、输入哈希和恢复方案后，按本项目规则取得
   对该次临时 boot_a 写入的明确确认。先取得 `sweetconn=1` 三个
   AP 值；本次用户的移植请求不等于后续每次分区写入授权。
2. 移植 MT6983 Conninfra 供电、PMIC、时钟、复位、bus protect、
   EMI remap/MPU、RF SPI/校准和超时退出。每步核对原厂顺序；
   AP gate 与 off-domain 可读性分开验证。先证明版本寄存器和
   ROM 启动状态，再访问下游无线寄存器。
3. 蓝牙：明确 ECO/flavor 固件对及不重叠 EMI 计划，加载后验证
   BGF ID `0x02060000`、ROM `0x1d1e`，接入 BTIF FIFO、时钟和
   driver/firmware ownership。先实测 HCI Reset / Read Local Version
   与 WMT FUNC_CTRL，再做扫描、ACL、LE 配对及断开/重置恢复。
4. Wi-Fi：验证 SOC7 ID / Conninfra `0x02050100` 和 WFSYS
   `0x02040100`，接入 WFDMA 固件下载、command/event rings、IRQ、
   DMA 生命周期及 cache/barrier；再证明扫描结果来自真实固件。
   随后完成 802.11 认证关联、WPA2 握手、数据收发、DHCP/DNS、
   断线重连及睡眠恢复，最后用实际网络访问验收。
5. iOS：按本机 iOS16 的真实用户态调用恢复 Wi-Fi / 蓝牙控制器
   IOKit/userclient ABI 与异步通知、权限和生命周期。当前没有
   `IOBluetooth` 控制器实现，历史日志中 `IONameMatch=bluetooth`
   匹配不到服务、`bluetoothd` 重复启动。仅注册一个同名空服务
   不能算完成。分别以设置中的真实扫描/连接/配对，以及实际
   传输数据作为最终验收；音频还需要后续 SCO/ISO 和音频路由。
