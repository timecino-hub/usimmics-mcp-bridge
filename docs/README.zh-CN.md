# uSimmics MCP Bridge

面向 uSimmics 5.9 的本地 MCP 接口，当前为 Alpha。可以在后台创建、保存、检查和预览电路，运行谐波平衡仿真，扫描参数并比较频谱。

需要 Python 3.11+、uv，以及单独下载的 uSimmics 5.9。仓库不包含模拟器、厂商元件库或课程讲义。

```powershell
uv tool install git+https://github.com/timecino-hub/usimmics-mcp-bridge.git
usimmics-bridge configure --installation-root "C:/Apps/uSimmics" --project-root "C:/CircuitProjects"
usimmics-bridge doctor
codex mcp add usimmics -- (Get-Command usimmics-mcp).Source
```

配置目录可用 `USIMMICS_BRIDGE_CONFIG` 指定。安装位置也可以通过 `USIMMICS_HOME` 指定。其他 MCP 客户端使用 `usimmics-mcp` 可执行文件的绝对路径作为 stdio 启动命令。

建议让助手按“读/搭电路 → 保存 → 校验 → 仿真 → 预览 → 保存结果”的流程工作。`examples/voltage_divider.json` 是不依赖厂商模型的起始例子，`examples/mixer.json` 则从本机库读取 BAT54。

本项目支持 Windows 上的原生 5.9 求解器；原版 Qucs、Qucs-S、任意元件、瞬态/DC/AC/SP 分析尚未覆盖。预览由接口自己绘制，不是原生窗口截图。所有操作针对磁盘文件，无法访问窗口中尚未保存的内容。跨进程锁只协调接口自身的写入，不会锁住原生 GUI。

完整参数、调用顺序和错误处理见 [工具说明](tools.md)。首次公开版本以可复现和明确的支持范围为目标。
