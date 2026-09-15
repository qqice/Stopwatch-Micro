# Stopwatch Micro

[English](README.md) | **简体中文**

[![Firmware build](https://github.com/Dissipative-ATLAS/Stopwatch-Micro/actions/workflows/firmware-build.yml/badge.svg?branch=main)](https://github.com/Dissipative-ATLAS/Stopwatch-Micro/actions/workflows/firmware-build.yml)
[![clang-format](https://github.com/Dissipative-ATLAS/Stopwatch-Micro/actions/workflows/clang-format-check.yml/badge.svg?branch=main)](https://github.com/Dissipative-ATLAS/Stopwatch-Micro/actions/workflows/clang-format-check.yml)

Stopwatch Micro 是一套面向 M5Stack StopWatch Dev Kit（ESP32-S3）的专用固件，把它变成一个
非官方的 Codex Micro 兼容控制器。设备开机后直接进入Command / History / Agent 多页面 LVGL 界面，并把低功耗蓝牙作为
系统服务持续运行；原厂 Mooncake 启动器和演示应用不包含在本固件中。

> 本项目通过非稳定公开 API 的协议实现非官方兼容层。ChatGPT Desktop 后续更新可能导致行为
> 变化，并需要同步更新固件或桥接程序。

## 产品示意图

<table>
  <tr>
    <td width="50%"><img src="docs/assets/stopwatch-micro-command-product.jpg" alt="Stopwatch Micro Command 界面产品示意图"></td>
    <td width="50%"><img src="docs/assets/stopwatch-micro-agents-product.jpg" alt="Stopwatch Micro Agent 界面产品示意图"></td>
  </tr>
  <tr>
    <td align="center">Command 界面</td>
    <td align="center">Agent 界面</td>
  </tr>
</table>

_产品概念示意图由项目所有者提供。_

## 主要功能与界面

- 六个带分层、高光与按压反馈的圆形 Command 按钮，环绕中央状态圆盘。
- 顶部弧形触控区用于降低、选择或提高推理强度。
- 中央圆盘显示 Codex 剩余额度、重置倒计时，以及 StopWatch 电池图标和百分比。
- History 为第二屏；Agent 页面保留六个会话选择键，连接主机时作为第三屏。
- 黄色实体 A 键控制电脑端按住说话，蓝色实体 B 键仅切换本地页面。
- BLE 配对信息持久保存，正常重启或重新上电后会自动重连。
- 60 秒无操作后进入 8% 亮度锁屏，每分钟刷新额度、电量并移动像素。

Command 和 Agent 控制需要蓝牙主机；配置 Wi-Fi 后，断开蓝牙仍可查看额度和 History。未配置网络的设备显示 Pairing。

| 输入 | 主机控制 | 功能 |
| --- | --- | --- |
| Command 左上 | `v.oai.rad` Plan 方向 | 开关 Plan 模式 |
| Command 左中 | `ACT06` | 开关 Fast 模式 |
| Command 左下 | `ACT07` | Approve，批准当前请求 |
| Command 右上 | `ACT11` | New Task，新建对话 |
| Command 右中 | `ACT09` | Fork，在新对话中继续 |
| Command 右下 | `ACT08` | Decline，拒绝当前请求 |
| 黄色实体 A 键 | `ACT10` | 按住调用电脑麦克风，松开结束 |
| 蓝色实体 B 键 | 本地界面 | Command → History → Agent；离线时跳过 Agent |
| 实体 A+B 键 | 本地界面 | 循环切换页面 |
| Agent 1–6 | `AG00`–`AG05` | 选择对应 Agent/对话 |
| 顶部推理弧 | `ENC_CC`、`ENC`、`ENC_CW` | 降低/提高推理强度，按下确认，随后回中 |
| 底部触摸传感器 | 本地系统 | 长按 3 秒清除 BLE 绑定并重新进入配对 |

`ACT10` 使用电脑当前选中的麦克风。本固件仅配置 StopWatch 音频输出，不启用 I2S 接收、
不采集内置 MEMS 麦克风，也不传输 PCM 音频。Mic 动画只是按住说话状态提示，不是音量表。

## Codex Micro 一次性设置

设备首次被 ChatGPT Desktop 识别后，打开 **Settings → Codex Micro**，完成以下主机端设置：

1. 将 **Dial** 设为 **Reasoning only**，否则顶部弧形区仍会执行默认的 Composer navigation。
2. 开启 **Use separate microphone keys**。
3. 选中独立的 `ACT11` 开关并设为 **New Task**。使用默认的合并麦克风键时，Codex Desktop
   会忽略独立 `ACT11` 事件，因此 New Task 不会响应。
4. 确认 **Analog Up → Toggle plan mode**。这是官方文档所列的默认映射，本项目的 Plan 按钮
   会发送该方向。

这些映射保存在 ChatGPT Desktop 中，而不是固件里；如果重置了 Codex Micro 布局，需要重新
检查以上设置。设置行为以 [Codex Micro 官方文档](https://learn.chatgpt.com/docs/features/codex-micro)
为准。

清除设备绑定后，如果 Windows 蓝牙设置中仍保留旧的 `Codex Micro`，请先删除旧条目，再重新
配对。正常复位或断电重启会自动恢复现有绑定；重连过程中 Pairing 页面可能短暂出现。

## 硬件与环境要求

- M5Stack StopWatch Dev Kit，ESP32-S3；本项目在 16 MiB Flash、8 MiB PSRAM 的硬件上验证。
- 首次备份和刷机需要可传输数据的 USB-C 线。
- 日常控制和无线额度更新可拔掉 USB 线，通过已配对的 Bluetooth HID 运行。
- Windows 无线额度桥接需要已登录的 ChatGPT Desktop、Python，以及 `hidapi`。
- 构建固件使用 ESP-IDF v6.1；依赖版本由 `repos.json` 和 `dependencies.lock` 固定。

## Windows 构建与刷机

仓库自带的 PowerShell 入口会检查工具链、发现 ESP32-S3 USB Serial/JTAG 端口，并在没有完整
备份时拒绝刷机。先把 `IDF_PATH` 设为你的 ESP-IDF v6.1 路径；示例端口 `COM5` 请替换为
你的实际端口：

```powershell
$env:IDF_PATH = 'C:\esp\v6.1\esp-idf'
.\tools\stopwatch.ps1 doctor -Port COM5
.\tools\stopwatch.ps1 deps -DirectGit
.\tools\stopwatch.ps1 build -SkipDeps
.\tools\stopwatch.ps1 backup -Port COM5
.\tools\stopwatch.ps1 flash -Port COM5 -Erase
```

第一次备份会读取完整的 16 MiB Flash。只有明确要恢复原厂固件时才运行：

```powershell
.\tools\stopwatch.ps1 restore -Port COM5 `
  -BackupPath .\.artifacts\backups\<timestamp>\stopwatch_factory_16MiB.bin `
  -ConfirmRestore
```

恢复操作会覆盖整片 Flash，请先核对设备、端口和备份文件。构建产物、备份、生成的依赖和发布
文件均被 Git 忽略。

Linux 或 macOS 的底层构建流程为：

```bash
python3 fetch_repos.py
source "$HOME/esp/esp-idf/export.sh"
idf.py build
idf.py -p /dev/cu.usbmodem21301 flash monitor
```

## 无线 Codex 额度桥接

Codex 账户额度不是标准 Codex Micro 控制协议的一部分，因此手表本身无法直接从互联网读取。
本项目使用以下本地链路：

```text
Codex App Server → Windows 桥接程序 → Bluetooth HID → StopWatch
```

安装依赖并启动桥接：

```powershell
python -m pip install hidapi
.\tools\stopwatch.ps1 bridge -Transport bluetooth
```

桥接程序使用 ChatGPT Desktop 管理的 Codex App Server 和当前登录状态，不需要 API Key 或单独
登录。保持进程运行即可持续刷新额度和重置时间；Bluetooth 模式不打开 `COM5`，因此 USB 线
可以拔掉。只更新一次可使用：

```powershell
.\tools\stopwatch.ps1 bridge -Transport bluetooth -Once
```

默认的 `auto` 传输会优先选择匹配的 Bluetooth HID，在 HID 不存在时才回退至 USB Serial/JTAG。
USB 回退还需要 `pyserial`：

```powershell
python -m pip install hidapi pyserial
.\tools\stopwatch.ps1 bridge
```

超过两分钟没有刷新时，手表会标记数据为过期；超过十分钟则显示不可用，不会伪造额度数值。
完整说明见 [`docs/bridge.md`](docs/bridge.md)。

安装登录 Windows 后自动运行的隐藏单实例任务：

```powershell
.\tools\bridge_autostart.ps1 install
.\tools\bridge_autostart.ps1 status
```

同一脚本还支持 `stop`、`start` 和 `remove`。自动启动仍依赖当前用户的 ChatGPT 登录会话和
蓝牙会话。

## 常亮与 AMOLED 注意事项

60 秒无本地操作后自动降至 8% 亮度，锁屏仅每分钟更新额度和电量。详见 [锁屏说明](docs/idle-display.md)。AMOLED 常亮仍会增加耗电，并加速 AMOLED
老化或烧屏。本项目保留每分钟一次的小幅像素漂移，它只能降低静态图像长期停留的风险，不能
完全消除烧屏。长期插电展示时建议降低亮度，并定期让屏幕休息。

## Web 界面预览

无需连接设备即可在 [`web/index.html`](web/index.html) 中检查交互。在线 GitHub Pages 版本：

<https://dissipative-atlas.github.io/Stopwatch-Micro/>

默认展示 Command 页面；使用 `?paired=0` 预览 Pairing，使用 `?mic=1` 预览 Mic 状态。页面模拟
六个圆形 Command 按钮、顶部推理弧、额度与电池状态，以及 A、B、A+B 实体键行为。

## 源码结构

```text
main/
├── main.cpp                         # 硬件服务与系统应用启动
├── system_config.h                  # BLE 产品身份与固件版本
├── debug/                           # USB Serial/JTAG 诊断 CLI
├── apps/
│   ├── app_codex_micro/             # 唯一应用与 LVGL 界面
│   └── common/                      # 公共按键/音频辅助模块
├── host/                            # 主机额度状态及超时逻辑
└── hal/
    ├── ble/                         # HID 与 JSON-RPC 兼容传输
    └── ...                          # StopWatch 板级支持
tools/                               # 构建、刷机、桥接、测试与打包工具
web/index.html                       # 浏览器界面原型
docs/                                # 架构、桥接与验证记录
```

`main/CMakeLists.txt` 使用显式源码清单，避免已删除的演示应用和资源被意外链接进固件。

## 发布包

从 [GitHub Releases](https://github.com/Dissipative-ATLAS/Stopwatch-Micro/releases) 下载公开发布的
自包含刷机 ZIP 和校验信息。也可以在干净提交上从源码复现：

```powershell
.\tools\stopwatch.ps1 package
```

发布包包含分区镜像、合并镜像、`flash.ps1`、机器可读刷机计划、已测试 Codex 版本、Windows
无线额度桥接、登录自启动工具、设置说明、许可证和 SHA-256 校验值。独立刷机包不会自动创建
原厂备份，首次刷机前仍应使用仓库的 `backup` 流程。

## 诊断与验证

固件通过 USB Serial/JTAG 提供非阻塞、逐行诊断 CLI，命令以 `debug` 或 `dbg` 开头。例如：

```text
debug status
debug selftest
debug controls
debug protocol
debug ui cycle
debug transport
debug perf 3000
debug trace 30000
```

未配对时使用离线验证；完成 Codex RPC 握手后使用严格验证：

```powershell
.\tools\stopwatch.ps1 verify -Port COM5 -AllowOffline
.\tools\stopwatch.ps1 verify -Port COM5
```

当前 v0.4.0 实机验证记录包括：HAL 自检 `17/17`、实体/触控控制 `13/13`、BLE
ready/connected/protocol `1/1/1`、无线额度更新拒绝数 `0`，以及
`HOST SUMMARY pass=10 skip=1 failures=0`。其中一个 `SKIP` 是刻意不执行会清除 BLE 绑定的
破坏性配对重置。完整测试边界、浏览器检查和硬件记录见 [`docs/validation.md`](docs/validation.md)。

## 参考来源与项目状态

- [Codex Micro 官方文档](https://learn.chatgpt.com/docs/features/codex-micro) 是配对、控制项、
  ChatGPT Desktop 行为和预期用户体验的权威参考。
- [`xuruiray/Stopwatch-Micro`](https://github.com/xuruiray/Stopwatch-Micro) 提供了最初的公开项目
  基础、仓库结构、板级支持集成和早期构建/界面工作。本仓库现为独立维护，同时保留并感谢这些
  上游贡献和历史。
- [`imliubo/codex-micro-4-core2`](https://github.com/imliubo/codex-micro-4-core2) 是非官方的
  逆向兼容实现，本项目参考其 BLE HID 身份、Codex Micro 控制 ID 和厂商 JSON-RPC 传输。
  审阅的上游版本为 [`2ee23a4`](https://github.com/imliubo/codex-micro-4-core2/commit/2ee23a4ab696f94bb78d250f28cc4a9b879ba079)。

## 贡献者

- **Dissipative-ATLAS** — 项目所有者与硬件集成。
- **OpenAI Codex** — AI 工程贡献者，参与固件、Windows 桥接、界面迭代、测试和文档工作。

这里对 Codex 的署名用于记录本社区项目中的 AI 辅助工程贡献，不代表 OpenAI 对本项目提供赞助、
背书或官方支持。

## 免责声明与许可证

本仓库是独立社区项目，与 OpenAI、ChatGPT、Codex、Work Louder、M5Stack 或
`codex-micro-4-core2` 维护者不存在隶属、授权、背书或支持关系。产品名和商标归各自权利人所有。
官方文档描述受支持产品的行为，但不表示底层厂商协议是稳定的公共 API。

本项目采用 [MIT License](LICENSE)。原始板级支持源自 M5Stack StopWatch 用户演示，兼容传输
改编自 [`imliubo/codex-micro-4-core2`](https://github.com/imliubo/codex-micro-4-core2)。相关上游
版本、版权和 MIT 许可声明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 历史与独立联网

第二屏 History 可切换 DAYS（绿色，官方每日 token）和 HOURS（蓝色，累计 token 的小时观察增量）。点击方块查看具体数值与数据质量；缺测、部分采样和计数修正会明确标记，不会伪造历史小时用量。详见 [历史说明](docs/history.md)、[局域网](docs/lan-service.md)、[Tailscale](docs/tailscale.md)。锁屏时第一次按键/触摸仅用于唤醒。

热力图按天显示近 30 天并标注日期，按小时显示近 24 小时并标注时间。Token 使用 K/M 自动换算，点击方块仍显示完整整数值。

锁屏默认启用省电策略：停止 Wi-Fi、断开蓝牙并停止广播，CPU 降至 80 MHz；每五分钟短暂联网更新额度，唤醒后恢复 240 MHz 并重连。每分钟显示缓存额度与电量。详见[省电策略与测量边界](docs/idle-power.md)。
