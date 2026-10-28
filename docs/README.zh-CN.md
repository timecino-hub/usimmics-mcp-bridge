# uSimmics MCP Bridge

让 AI 助手替你搭建、仿真和检查 **uSimmics 5.9** 电路。它是一个本地 [MCP](https://modelcontextprotocol.io) 服务器：助手直接编辑磁盘上的 `.sch` 文件，在后台调用 uSimmics 自带的谐波平衡求解器，再把电路图和频谱图返回给你，全程不需要操作桌面界面。

> **Alpha 版 0.3.0a1。** 原生仿真已在 Windows + uSimmics 5.9 上测试。本项目是独立实现，不是 uSimmics/QucsStudio 官方产品；原版 Qucs 和 Qucs-S 的文件格式不同。

[English](../README.md) · [工具说明（英文）](tools.md) · [参与贡献（英文）](../CONTRIBUTING.md)

![自带 BAT54 混频器示例的预览：上方是电路图，下方是标注了混频分量的谐波平衡频谱](images/mixer-preview-v2.png)

## 可以这样用

- “新建一个项目，搭一个 1 MHz 的分压电路，仿真后给我看频谱。”
- “运行 BAT54 混频器示例，告诉我 1 MHz 中频的功率。”
- “把 R1 分别设成 50、100、200 Ohm 扫描一遍，比较 1 MHz 中频电平。”
- “复制混频器，把本振改成 12 MHz，比较两个版本的中频功率。”

助手会用接口提供的 16 个工具完成这些请求，工具列表见[英文说明](../README.md#tools)。

## 快速开始

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)，以及另外安装的 [uSimmics 5.9](https://qucsstudio.de/download/)。本仓库不包含、也不会下载模拟器、厂商元件库或课程讲义。

```powershell
uv tool install git+https://github.com/timecino-hub/usimmics-mcp-bridge.git
usimmics-bridge configure --installation-root "C:/Apps/uSimmics" --project-root "C:/CircuitProjects"
usimmics-bridge doctor
```

`doctor` 输出 JSON；在模拟器、元件库和项目目录都就绪之前，它会以非零状态退出。安装位置也可以用 `USIMMICS_HOME` 指定，配置文件位置可以用 `USIMMICS_BRIDGE_CONFIG` 指定。

## 连接 MCP 客户端

服务器命令是 `usimmics-mcp`：通过 stdio 通信，不开网络端口，也不需要 API key 或云服务。图形界面客户端可能读不到终端的 `PATH`，所以请填写 `(Get-Command usimmics-mcp).Source` 输出的绝对路径。

```powershell
# Claude Code（--scope user 表示所有项目都能使用）
claude mcp add --transport stdio --scope user usimmics -- (Get-Command usimmics-mcp).Source
# Codex
codex mcp add usimmics -- (Get-Command usimmics-mcp).Source
```

Claude Desktop 等使用 JSON 配置的客户端：

```json
{
  "mcpServers": {
    "usimmics": { "command": "C:\\Users\\you\\.local\\bin\\usimmics-mcp.exe" }
  }
}
```

客户端重新加载工具之前，可以先在终端用 `usimmics-mcp-call list` 或 `usimmics-mcp-call environment_status` 通过真实的 MCP 通道测试，结果以 JSON 和图片路径显示。

## 预览图

`preview_schematic` 返回一张由接口根据磁盘文件绘制的 PNG（不是 uSimmics 窗口截图），以及 JSON 元数据：

- **电路图**：元件和导线保持文件中的坐标；连接点圆点只标真正的 T 形连接，单纯交叉的导线不相连；网络标签画成带引线的标签；会互相遮挡的文字标签会被挪到最近的空位，并列在 `adjusted_labels` 中。
- **频谱**：每个节点电压单独一个面板，共用频率轴；功率按 50 Ω 换算为 dBm（交流取峰值，直流取有效值），显示下限 −140 dBm。最强的几条谱线标注混频分量（如 `f1−f2`）、频率和功率，`spectrum_markers` 返回同样的精确数值。原始数据不会被修改。
- **数据来源**：如果数据集与当前保存的电路不匹配，图中和 `warnings` 里都会给出警告。

## 支持范围

- 文件格式：只支持 `QucsStudio Schematic 5.9`。
- 元件：R、C、L、Vac、Vdc、Diode、Filter、GND，以及一个谐波平衡（HB）仿真块。BAT54 从本机安装的元件库读取。Vdc 可以作为 HB 电路中的元件使用，但不提供 DC 工作点分析。
- 尚未支持：瞬态、AC、S 参数分析，嵌套扫描，任意器件模型，镜像元件，原生图表标记及其他图表类型。遇到不支持的内容会明确报错，而不是猜测。

## 安全与数据完整性

- 所有操作都针对磁盘文件，无法访问 uSimmics 窗口中尚未保存的内容。保存前会校验格式和连接关系；覆盖已有文件需要提供读取时的 SHA256，并自动备份。
- 每次仿真和扫描都使用独立的输出目录。参数扫描不修改原文件，最多 20 个点，每次求解限时 30 秒。比较结果只用真实采样点，不做插值。
- 跨进程锁只协调接口自身的写入，不会锁住 uSimmics 图形界面；在界面里覆盖保存之前，请先重新加载文件。
- 配置的项目目录只能防止误操作，并不是操作系统级的沙箱。详见 [SECURITY.md](../SECURITY.md)（英文）。

建议让助手按“读取或搭建电路 → 保存 → 校验 → 仿真 → 预览 → 保存结果”的顺序工作。完整参数和错误处理见[工具说明](tools.md)。
