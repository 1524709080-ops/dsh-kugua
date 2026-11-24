#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install.py —— dsh-bridge 分享包的一键安装器（跨平台：Windows / macOS / Linux）。

它做三件事：
  1. 挑一个可用的 python（优先 DSH 自带的 python，因为它通常已装好 mcp/zstandard）。
  2. 把依赖装进去：mcp、zstandard。
  3. 生成各 MCP 客户端（codex / Claude Code / Cursor / Windsurf 等）的配置，打印出来，
     并可选择直接写入对应客户端的配置文件。

用法：
    python install.py              检测 + 装依赖 + 打印各客户端配置片段
    python install.py --codex      额外把配置写入 ~/.codex/config.toml（若有 codex）
    python install.py --no-install  跳过依赖安装，只生成配置
    python install.py --python <路径>  强制使用指定 python

依赖安装需要网络。若 pip 下载慢，可加 --mirror https://pypi.tuna.tsinghua.edu.cn/simple
"""
import os
import sys
import glob
import json
import shutil
import argparse
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))

# 三个脚本里，MCP server 是核心；codex_bridge.py 是可选的反向读取工具。
MCP_SERVER = os.path.join(_HERE, "dsh_mcp_server.py")


# ---------------------------------------------------------------------------
# 1. 检测可用的 python
# ---------------------------------------------------------------------------

def _expanduser(p):
    return os.path.expanduser(p)


def _dsh_python_candidates():
    """DSH 自带 python 的常见位置（跨平台通配）。"""
    home = _expanduser("~")
    pats = [
        os.path.join(home, ".dsh", "dsh-runtimes", "*", "dependencies", "python", "python.exe"),
        os.path.join(home, ".dsh", "dsh-runtimes", "*", "dependencies", "python", "python"),
        os.path.join(home, ".dsh", "dsh-runtimes", "*", "dependencies", "python", "python3"),
        os.path.join(home, ".dsh", "dsh-runtimes", "*", "python", "python.exe"),
        os.path.join(home, ".dsh", "dsh-runtimes", "*", "python", "python"),
    ]
    out = []
    for p in pats:
        for hit in sorted(glob.glob(p)):
            if os.path.isfile(hit):
                out.append(hit)
    return out


def pick_python(forced=None):
    """返回 (python_path, 来源说明)。"""
    cands = []
    if forced:
        cands.append((forced, "命令行指定"))
    for p in _dsh_python_candidates():
        cands.append((p, "DSH 自带 python"))
    cands.append((sys.executable, "当前运行的 python"))
    for name in ("python3", "python"):
        w = shutil.which(name)
        if w:
            cands.append((w, f"PATH 里的 {name}"))

    seen = set()
    for path, src in cands:
        if path in seen or not path:
            continue
        seen.add(path)
        if os.path.isfile(path):
            return path, src
    return None, "未找到 python"


# ---------------------------------------------------------------------------
# 2. 安装依赖
# ---------------------------------------------------------------------------

def pip_install(python, mirror=None):
    cmd = [python, "-m", "pip", "install", "--upgrade", "mcp", "zstandard"]
    if mirror:
        cmd += ["-i", mirror]
    print(f"\n→ 正在安装依赖：mcp、zstandard\n   （{python}）")
    print("   $", " ".join(cmd), "\n")
    r = subprocess.call(cmd)
    return r == 0


# ---------------------------------------------------------------------------
# 3. 生成各客户端配置
# ---------------------------------------------------------------------------

def _quote_toml(s):
    """TOML 基本字符串里的转义（反斜杠、单引号）——用于 Windows 路径。"""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def configs(python, script):
    """返回 {客户端名: (说明, 配置文本)}。command=python，args=[script] 用绝对路径。"""
    results = {}

    # —— codex ——
    codex_toml = (
        "[mcp_servers.dsh_bridge]\n"
        f"command = '{_quote_toml(python)}'\n"
        f"args = ['{_quote_toml(script)}']\n"
        "startup_timeout_sec = 60\n"
    )
    results["codex"] = (
        "追加到 ~/.codex/config.toml（桌面版 codex 的配置文件）",
        codex_toml,
    )

    # —— Claude Code ——
    cc_cmd = f'claude mcp add dsh-bridge -- "{python}" "{script}"'
    cc_json = json.dumps({
        "mcpServers": {
            "dsh_bridge": {
                "command": python,
                "args": [script],
            }
        }
    }, ensure_ascii=False, indent=2)
    results["claude_code"] = (
        f"在终端执行：{cc_cmd}\n或写入 ~/.claude.json 的 mcpServers 段：",
        cc_json,
    )

    # —— Cursor / Windsurf / 通用（JSON 格式） ——
    generic = json.dumps({
        "mcpServers": {
            "dsh_bridge": {
                "command": python,
                "args": [script],
            }
        }
    }, ensure_ascii=False, indent=2)
    results["cursor_windsurf_generic"] = (
        "在 Cursor / Windsurf 的 MCP 设置里，添加一个 stdio server，填同样的 command/args：",
        generic,
    )

    # —— WorkBuddy（腾讯） ——
    results["workbuddy"] = (
        "写入 ~/.workbuddy/mcp.json（用户级，所有项目复用；或项目内 .workbuddy/mcp.json）：",
        json.dumps({
            "mcpServers": {
                "dsh_bridge": {"command": python, "args": [script]},
            }
        }, ensure_ascii=False, indent=2),
    )

    # —— Trae / 豆包编程（MarsCode） ——
    results["trae_marscode"] = (
        "写入 .trae/mcp.json（项目级）或全局 mcp.json：",
        json.dumps({
            "mcpServers": {
                "dsh_bridge": {"command": python, "args": [script]},
            }
        }, ensure_ascii=False, indent=2),
    )

    return results


# ---------------------------------------------------------------------------
# 4. 写入 codex 配置（可选）
# ---------------------------------------------------------------------------

def write_codex_config(python, script):
    cfg = _expanduser("~/.codex/config.toml")
    if not os.path.isfile(cfg):
        print(f"\n⚠ 未找到 {cfg}，跳过 codex 配置写入（你仍可手动粘贴上面的片段）。")
        return False
    block = configs(python, script)["codex"][1]
    with open(cfg, encoding="utf-8") as f:
        content = f.read()
    if "dsh_bridge" in content:
        print(f"\n⚠ {cfg} 里已有 dsh_bridge 配置，跳过（避免重复）。")
        return False
    bak = cfg + ".bak-dshbridge"
    if not os.path.exists(bak):
        shutil.copyfile(cfg, bak)
        print(f"→ 已备份原配置到 {bak}")
    with open(cfg, "a", encoding="utf-8") as f:
        f.write("\n" + block)
    print(f"→ 已把 dsh_bridge 配置追加到 {cfg}")
    print("   （重启 codex 后，codex 即可调用 list/dump/tail_dsh_session 三个工具）")
    return True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="dsh-bridge 一键安装器")
    ap.add_argument("--python", help="强制使用指定 python 解释器")
    ap.add_argument("--codex", action="store_true", help="把配置写入 ~/.codex/config.toml")
    ap.add_argument("--no-install", action="store_true", help="跳过依赖安装")
    ap.add_argument("--mirror", help="pip 镜像，如 https://pypi.tuna.tsinghua.edu.cn/simple")
    args = ap.parse_args()

    print("=" * 66)
    print("dsh-bridge 安装器 —— 让任意 MCP 客户端读取 DSH（DeepSeek Harness）对话")
    print("=" * 66)

    python, src = pick_python(args.python)
    if not python:
        print("\n❌ 找不到可用的 python。请先安装 python，或用 --python 指定路径。")
        sys.exit(1)
    print(f"\n① 使用 python：{python}  （{src}）")

    script = os.path.abspath(MCP_SERVER)
    if not os.path.isfile(script):
        print(f"\n❌ 找不到 {script}，请确认 dsh_mcp_server.py 与本脚本在同一目录。")
        sys.exit(1)
    print(f"② MCP server 脚本：{script}")

    if not args.no_install:
        if not pip_install(python, args.mirror):
            print("\n⚠ 依赖安装似乎失败（可能网络问题）。")
            print("  可重试：加 --mirror 用国内镜像；或手动执行：")
            print(f"    \"{python}\" -m pip install mcp zstandard")
        else:
            print("✓ 依赖安装完成。")
    else:
        print("（已跳过依赖安装）")

    print("\n" + "=" * 66)
    print("③ 各客户端 MCP 配置（command / args 已填成你的绝对路径）")
    print("=" * 66)
    for name, (desc, text) in configs(python, script).items():
        print(f"\n【{name}】 {desc}")
        print("-" * 60)
        print(text)

    if args.codex:
        write_codex_config(python, script)

    print("\n" + "=" * 66)
    print("完成。重启对应客户端后，即可让它们调用：")
    print("  list_dsh_sessions / dump_dsh_session / tail_dsh_session")
    print("=" * 66)


if __name__ == "__main__":
    main()
