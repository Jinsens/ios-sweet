# Sweet GPU Bench

为 ios-sweet / MT6983 编写的原生 iOS GPU 测试应用。帧率只有 **60 / 120 FPS**
两档，负载 1–8 档，每轮 20 秒，也可手动停止。应用在独立队列运行 Panfrost
OpenGL ES 3 工作负载，每帧等待 GPU 完成后计数，展示完成帧率、平均/P95
完成耗时、目标节拍丢失数和曲线，并可导出 JSON。

当前测试使用 512×512 离屏缓冲区。GPU 完成帧率与 CADisplayLink 的通知速率
分别显示。选择 120 只调整测试任务的目标速率；当前版本不会发送面板命令，
不会把 60Hz 面板宣称为 120Hz。完成耗时包含提交和等待，不是硬件 GPU 时间戳。
硬件渲染器必须同时包含 `G710` 和 `Panfrost`，软件回退会报告错误。

## 构建

需要 Xcode 的 iPhoneOS SDK；运行时使用设备已有的硬件 Mesa 库。源码不包含 Apple SDK、设备
数据或下载的第三方库。默认输出位于 git 忽略的 `research/` 下。

```sh
python3 tools/build-gpu-bench.py
```

产出 `research/matisse/gpu-bench-out/SweetGPUBench.ipa`、`.app`、签名验证和
SHA256 清单。默认不捆绑库，使用设备上已有的
`/usr/local/sweet-gpu/lib/libOSMesa.8.dylib`，避免用较旧的本地构建覆盖新后端。
确需自带库时，可加 `--mesa /绝对路径/libOSMesa.8.dylib`；脚本会把它与非系统
依赖复制到应用的 `Frameworks/`，并修正相对加载路径。应先确认该库与当前内核兼容。

此包为当前 XNU 移植环境临时签名，不能直接安装在标准未越狱 iPhone 上。
构建脚本不会安装应用或修改手机。该设备的 iOS 根位于 `userdata`，安装会
修改此分区，需要按项目规则单独确认。不要在 Recovery 中直接写应用或运行
旧测试脚本；先制作可审阅的安装和恢复方案。

## 测量约定

- FPS 仅统计 `glFinish` 成功返回的完整离屏帧，目标值不作为测量值。
- 运行中显示最近采样区间的统计；停止后显示整轮完成帧率、平均耗时和 P95。
- 比较两轮结果时使用相同负载、分辨率和内核；启动后的首次着色器编译不计入帧耗时。
- 进入后台立即停止测试，回到前台后由用户重新开始。
- 目标 120 FPS 对应 8.33ms 节拍。若实际渲染更慢，丢弃过期节拍，不累积补帧队列。
- 真正的屏幕 120Hz 仍需显示驱动完成厂商模式切换并以 TE 和扫描输出验证。

主机验证：`python3 -m unittest tools.tests.test_gpu_bench -v`。测试运行真实引擎
代码与模拟 Mesa，覆盖节拍、完成计数、故障、取消与资源释放；不代表 Mali 性能结果。
当前 IPA 已完成 arm64 构建与签名、包完整性检查，尚未在设备上启动测试。
