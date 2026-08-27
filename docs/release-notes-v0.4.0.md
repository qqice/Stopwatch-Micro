# Stopwatch Micro v0.4.0

The first public binary release of Stopwatch Micro, an unofficial Codex Micro-compatible firmware
for the M5Stack StopWatch Dev Kit (ESP32-S3).

## Highlights

- Six tactile circular controls: Plan, New Task, Fast, Fork, Approve, and Decline.
- A touch-sensitive reasoning arc for changing and selecting reasoning effort.
- A central status dial showing Codex quota remaining, reset countdown, and watch battery.
- Wireless quota updates over paired Bluetooth HID; USB is not required for daily use.
- Always-on AMOLED with periodic pixel shifting.
- Persistent BLE bonding and hardened automatic reconnection.
- Windows backup, build, flash, restore, diagnostics, wireless bridge, and logon-startup tools.

## One-time Codex Desktop setup

Open **Settings → Codex Micro** and configure:

1. **Dial → Reasoning only**
2. Enable **Use separate microphone keys**
3. Assign independent `ACT11` to **New Task**
4. Confirm **Analog Up → Toggle plan mode**

These mappings are stored by Codex Desktop rather than by the firmware.

## Wireless quota bridge

```powershell
python -m pip install hidapi
.\tools\stopwatch.ps1 bridge -Transport bluetooth
.\tools\bridge_autostart.ps1 install
```

The bridge uses the desktop-managed Codex App Server and current login. It requires no separate API
key and copies no authentication token. The watch can run from its battery with USB disconnected.

## Validation

Validated with ESP-IDF 5.5.4, Windows, Codex Desktop 26.820.7780.0, and the 16 MiB flash / 8 MiB
PSRAM M5Stack StopWatch ESP32-S3:

- HAL self-test: 17/17
- Controls: 13/13
- BLE ready/connected/protocol: 1/1/1
- 50 Hz transport: 151/151, zero dropped reports
- Wireless quota frames: zero rejected
- Host suite: `pass=10 skip=1 failures=0`

Download the ZIP and `.sha256` file, verify the checksum, and read `FLASHING.md`. Preserve a complete
factory backup before the first installation. The ZIP includes all firmware images, flashing tools,
bridge helpers, manifest, checksums, license, and third-party notices.

## Contributors

- **Dissipative-ATLAS** — project owner and hardware integration
- **OpenAI Codex** — AI engineering contributor for firmware, Windows bridge, UI iteration, testing,
  and documentation

The Codex credit records AI-assisted engineering work; it does not imply OpenAI sponsorship,
endorsement, or official support. This independent community project is not affiliated with OpenAI,
ChatGPT, Codex, Work Louder, or M5Stack. The compatibility protocol is not a stable public API.

---

# Stopwatch Micro v0.4.0 中文版

Stopwatch Micro 的首个公开二进制版本：面向 M5Stack StopWatch Dev Kit（ESP32-S3）的非官方
Codex Micro 兼容固件。

## 主要功能

- 六个具有分层质感和按压反馈的圆形按钮：Plan、New Task、Fast、Fork、Approve、Decline。
- 顶部弧形触控区可调整并选择推理强度。
- 中央状态圆盘显示 Codex 剩余额度、重置倒计时和手表电量。
- 通过已配对的 Bluetooth HID 无线更新额度，日常使用不需要 USB。
- AMOLED 常亮，并通过周期性像素漂移降低静态画面磨损。
- BLE 绑定持久保存，增强断线和重新上电后的自动重连。
- 提供 Windows 备份、构建、刷机、恢复、诊断、无线桥接和登录自启动工具。

## Codex Desktop 一次性设置

打开 **Settings → Codex Micro**：

1. 设置 **Dial → Reasoning only**
2. 开启 **Use separate microphone keys**
3. 将独立的 `ACT11` 分配为 **New Task**
4. 确认 **Analog Up → Toggle plan mode**

这些映射保存在 Codex Desktop 中，而不是固件内。

## 无线额度桥接

```powershell
python -m pip install hidapi
.\tools\stopwatch.ps1 bridge -Transport bluetooth
.\tools\bridge_autostart.ps1 install
```

桥接程序使用 Codex Desktop 管理的 App Server 和当前登录状态，不需要单独的 API Key，也不会
复制认证令牌。拔掉 USB 后，手表仍可依靠电池运行并持续更新额度。

## 验证结果

已使用 ESP-IDF 5.5.4、Windows、Codex Desktop 26.820.7780.0，以及 16 MiB Flash / 8 MiB
PSRAM 的 M5Stack StopWatch ESP32-S3 验证：

- HAL 自检：17/17
- 控制项：13/13
- BLE ready/connected/protocol：1/1/1
- 50 Hz 传输：151/151，零丢包
- 无线额度帧：零拒绝
- Host 测试：`pass=10 skip=1 failures=0`

请下载 ZIP 和 `.sha256` 文件，校验后阅读包内 `FLASHING.md`；首次安装前应保存完整原厂备份。
ZIP 包含全部固件镜像、刷机工具、桥接助手、manifest、校验值、许可证和第三方声明。

## 贡献者

- **Dissipative-ATLAS** — 项目所有者与硬件集成
- **OpenAI Codex** — AI 工程贡献者，参与固件、Windows 桥接、界面迭代、测试和文档工作

这里对 Codex 的署名用于记录 AI 辅助工程贡献，不代表 OpenAI 对项目提供赞助、背书或官方支持。
本项目是独立社区项目，与 OpenAI、ChatGPT、Codex、Work Louder 或 M5Stack 不存在隶属关系；
兼容协议也不是稳定的公共 API。
