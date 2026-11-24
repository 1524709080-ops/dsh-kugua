#!/usr/bin/env python3
"""
dsh_mcp_server.py —— 让任意 MCP 客户端（codex / Claude Code / Cursor 等）
直接调用 DSH 对话读取能力。

这是一个标准 stdio MCP server，暴露 3 个工具：
  list_dsh_sessions    列出所有 DSH 会话
  dump_dsh_session     导出某个会话的完整对话
  tail_dsh_session     看某个会话最后 N 条消息

用法：在目标客户端的 MCP 配置里加一段（command=你的 python，args=本脚本路径），
详见 README.md 和 install.py（会自动生成各客户端配置）。
依赖：mcp、zstandard（pip install mcp zstandard）。
"""
import os
import sys

# 允许 import 同目录的 dsh_bridge.py
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from dsh_bridge import (  # noqa: E402
    find_sessions,
    _decompress,
    _parse_lines,
    _render_events,
    _fmt_ts,
    _pick_session,
)

from mcp.server.mcpserver import MCPServer  # noqa: E402

mcp = MCPServer("dsh-bridge")


def _sessions_text():
    sessions = find_sessions()
    if not sessions:
        return "未找到任何 DSH 会话。"
    lines = [f"共 {len(sessions)} 个会话：", ""]
    for i, m in enumerate(sessions):
        title = m["title"] or "(无标题)"
        created = _fmt_ts(m["created_at"]) if m["created_at"] else "?"
        lines.append(f"[{i}] {title}")
        lines.append(f"    id: {m['id']}")
        lines.append(f"    cwd: {m['cwd']}")
        lines.append(f"    时间: {created}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def list_dsh_sessions() -> str:
    """列出 DeepSeek Harness (DSH) 里的所有对话会话，含序号、标题、id、工作目录和时间。
    序号 [N] 可传给 dump_dsh_session / tail_dsh_session 的 target 参数。"""
    return _sessions_text()


@mcp.tool()
def dump_dsh_session(target: str = "0", show_reasoning: bool = True) -> str:
    """导出某个 DSH 会话的完整对话内容。

    target：会话序号（如 "0"）、会话 id 前缀、或标题关键词。
    show_reasoning：是否包含模型的思考过程（reasoning），默认 True。"""
    sessions = find_sessions()
    m = _pick_session(sessions, target)
    if not m:
        return "未找到匹配的会话。先调用 list_dsh_sessions 查看可用序号。\n" + _sessions_text()
    events = _parse_lines(_decompress(m["path"]))
    header = (
        f"# {m['title'] or '(无标题)'}\n"
        f"# id: {m['id']}  cwd: {m['cwd']}\n"
        f"# 时间: {_fmt_ts(m['created_at']) if m['created_at'] else '?'}\n\n"
    )
    return header + _render_events(events, show_reasoning=show_reasoning)


@mcp.tool()
def tail_dsh_session(target: str = "0", n: int = 10, show_reasoning: bool = False) -> str:
    """看某个 DSH 会话最后 N 条消息。

    target：会话序号（如 "0"）、会话 id 前缀、或标题关键词。
    n：返回最近多少条消息，默认 10。
    show_reasoning：是否包含思考过程，默认 False。"""
    sessions = find_sessions()
    m = _pick_session(sessions, target)
    if not m:
        return "未找到匹配的会话。先调用 list_dsh_sessions 查看可用序号。\n" + _sessions_text()
    events = _parse_lines(_decompress(m["path"]))
    rendered = _render_events(events, show_reasoning=show_reasoning)
    blocks = [b for b in rendered.split("\n\n") if b.strip()]
    header = f"# {m['title'] or '(无标题)'}（最近 {min(n, len(blocks))} 条）\n\n"
    return header + "\n\n".join(blocks[-n:])


if __name__ == "__main__":
    mcp.run()


# ============================================================================
# 各客户端 MCP 配置示例（command 填你的 python 路径，args 填本脚本绝对路径）：
#
# 【codex】 在 ~/.codex/config.toml 里追加：
#   [mcp_servers.dsh_bridge]
#   command = 'python'
#   args = ['/绝对路径/dsh_mcp_server.py']
#   startup_timeout_sec = 60
#
# 【Claude Code】 在 ~/.claude.json 或 `claude mcp add dsh-bridge -- python 脚本路径`
# 【Cursor / Windsurf】 在 MCP 设置里填同样的 command + args
#
# 运行 install.py 会自动检测 python、安装依赖并生成这些配置。
# ============================================================================
