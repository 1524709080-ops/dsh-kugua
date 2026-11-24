# dsh-bridge

让任意 **MCP 客户端**（codex、Claude Code、Cursor、Windsurf、Cline …）读取
**DeepSeek Harness (DSH)** 的对话；同时提供命令行工具读取 **codex** 的对话。

> 核心是一个标准的 stdio MCP server，不绑定任何特定客户端——只要能配 MCP，就能接。

---

## 三个脚本各干什么

| 文件 | 作用 | 方向 | 依赖 |
|---|---|---|---|
| `dsh_mcp_server.py` | MCP server，暴露 3 个工具给任意客户端 | 客户端 → 读 DSH | `mcp`、`zstandard` |
| `dsh_bridge.py` | 命令行工具，直接读 DSH 对话 | 你 → 读 DSH | `zstandard` |
| `codex_bridge.py` | 命令行工具，读 codex 对话 | DSH / 你 → 读 codex | `zstandard`（可选） |

暴露的 3 个 MCP 工具：

- `list_dsh_sessions` —— 列出所有 DSH 会话（序号 / 标题 / id / 工作目录 / 时间）
- `dump_dsh_session(target, show_reasoning)` —— 导出某个会话的完整对话
- `tail_dsh_session(target, n, show_reasoning)` —— 看某个会话最后 N 条消息

---

## 兼容性 / 适配范围

### 支持的 MCP 客户端

任何实现 **MCP stdio transport** 的客户端都能接，包括（不限于）：

- **codex**（桌面版 / CLI）
- **Claude Code**
- **Cursor**
- **Windsurf**
- **WorkBuddy**（腾讯）
- **Trae / 豆包编程（MarsCode）**（字节）
- **Cline / Roo Code / Continue**

### 平台

- **Windows / macOS / Linux** 均可。脚本是纯 Python，只依赖标准库 + `mcp` + `zstandard`，
  无平台特定代码；`install.py` 会自动处理路径分隔符和可执行文件名差异。

### 前提

- **Python 3.8+**（推荐 3.10+）。
- DSH **无需正在运行**——脚本直接读磁盘上的会话文件。
- codex **无需正在运行**——`codex_bridge.py` 直接读其本地存储。

### 版本兼容

| 对象 | 读取的格式 | 说明 |
|---|---|---|
| DSH 会话 | `session.v4.jsonl.zstd`（v4） | 对未知事件类型是「跳过」而非报错，向前兼容 |
| codex 会话 | `state_5.sqlite` + `rollout-*.jsonl(.zst)` + `thread_history_1.sqlite` | 旧会话（`.zst`）已支持解压 |

若上游（DSH / codex）升级改变了存储格式，可能需要对解析逻辑做小幅更新；
脚本对不认识的字段一律 `get()` 兜底，不会因单条异常数据崩溃。

### 局限

- **只读**：不会写入、修改或删除任何对话。
- **非实时**：每次调用读取一次磁盘快照，不做持续推送。

---

## 快速开始（三步）

### 第 1 步：安装依赖

```bash
python install.py
```

它会自动挑一个可用的 python、安装 `mcp` + `zstandard`，并打印各客户端配置。
（网络慢可加 `--mirror https://pypi.tuna.tsinghua.edu.cn/simple`）

也可以手动装：

```bash
pip install mcp zstandard
```

### 第 2 步：配置你的客户端

把 `install.py` 打印的配置填进对应客户端（`command` 和 `args` 都已填成绝对路径）。
也可直接 `python install.py --codex` 自动写入 codex 配置。

### 第 3 步：重启客户端，开聊

重启后，客户端里就能调用 `list_dsh_sessions` / `dump_dsh_session` / `tail_dsh_session`。

---

## 各客户端 MCP 配置

### codex（桌面版）

`~/.codex/config.toml` 追加：

```toml
[mcp_servers.dsh_bridge]
command = '/你的/python/路径/python'
args = ['/绝对路径/dsh_mcp_server.py']
startup_timeout_sec = 60
```

### Claude Code

```bash
claude mcp add dsh-bridge -- "/你的/python/路径/python" "/绝对路径/dsh_mcp_server.py"
```

### Cursor / Windsurf / 其它（通用 JSON 格式）

```json
{
  "mcpServers": {
    "dsh_bridge": {
      "command": "/你的/python/路径/python",
      "args": ["/绝对路径/dsh_mcp_server.py"]
    }
  }
}
```

### WorkBuddy（腾讯）

写入 `~/.workbuddy/mcp.json`（用户级，所有项目复用），或项目内的 `.workbuddy/mcp.json`：

```json
{
  "mcpServers": {
    "dsh_bridge": {
      "command": "/你的/python/路径/python",
      "args": ["/绝对路径/dsh_mcp_server.py"]
    }
  }
}
```

### Trae / 豆包编程（MarsCode）

写入 `.trae/mcp.json`（项目级）或全局 `mcp.json`，格式同上（标准 `mcpServers` JSON）。

> 注意：豆包的**对话 App / 网页版**不支持自定义 MCP server，只有 **Trae / 豆包编程 IDE** 支持。

> **Windows 提示**：TOML 单引号字符串里反斜杠要转义成 `\\`（`install.py` 已自动处理）。
> 另外 Windows 的 `python` 命令可能是 Microsoft Store 的空壳（运行即闪退），
> 请用真实的 python 路径，或 `py -3`。

---

## 命令行直接读（不经过 MCP）

```bash
# 读 DSH 会话
python dsh_bridge.py list              # 列出所有会话
python dsh_bridge.py dump 0            # 导出第 0 个会话完整对话
python dsh_bridge.py tail 0 10         # 看第 0 个会话最后 10 条

# 读 codex 会话（反向）
python codex_bridge.py list            # 列出所有 codex 会话
python codex_bridge.py dump 0          # 导出第 0 个会话
python codex_bridge.py latest 5        # 看最近 5 个会话
```

`dump` / `tail` 的 `target` 可以是序号、会话 id 前缀、或标题关键词。

---

## 原理：数据存在哪

### DSH 的会话

```
~/.dsh/sessions/<工作目录编码>/<session-id>/session.v4.jsonl.zstd
```

- 是 **zstd 流式压缩** 的 JSONL，每行一个事件，`type` 字段区分类型
  （`user/message`、`assistant/message`、`tool/call`、`tool/result`、`session/title` …）。
- 时间戳是标准 Unix 毫秒。
- 解压用 `zstandard` 的跨帧 `stream_reader`（普通 `decompress()` 会因无 content size 报错）。

### codex 的会话

```
~/.codex/state_5.sqlite                       # 会话元数据（threads 表）
~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl(.zst)   # 逐条事件（权威，旧会话是 .zst）
~/.codex/thread_history_1.sqlite              # 旧版兜底（thread_items 表）
```

- `rollout-*.jsonl` 是逐条写入的权威来源；旧会话被压缩成 `.jsonl.zst`。
- 解析时会过滤掉 codex 的环境注入 / developer 系统消息，只保留真实对话。

---

## 常见问题

**Q：装完还是连不上？**
先确认 MCP server 能单独跑起来：直接执行 `python dsh_mcp_server.py`，看到
JSON-RPC 初始化响应（`serverInfo.name = "dsh-bridge"`）就说明 server 没问题，问题在客户端配置。

**Q：`python` 命令点了没反应（Windows）？**
那是 Microsoft Store 的空壳 python。用 `py -3` 或真实 python 的完整路径。

**Q：能读到思考过程（reasoning）吗？**
能。`dump_dsh_session` / `tail_dsh_session` 的 `show_reasoning` 参数控制是否包含思考。

**Q：只支持 DSH 和 codex 吗？**
DSH 这端是标准 MCP server，任何支持 MCP 的客户端都能接。codex 这端是
`codex_bridge.py` 只读它的本地存储，不依赖 codex 在不在运行。

---

## 目录

```
dsh-bridge/
├── README.md            本文档
├── README_EN.md         English version
├── install.py           一键安装器（检测 python + 装依赖 + 生成配置）
├── dsh_mcp_server.py    MCP server（核心）
├── dsh_bridge.py        命令行读 DSH
└── codex_bridge.py      命令行读 codex（反向，可选）
```

## License

MIT
