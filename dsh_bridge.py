#!/usr/bin/env python3
"""
dsh_bridge.py —— 让 codex（或其他工具）读取 DeepSeek Harness (DSH) 的对话。

DSH 把每个会话存成：
    ~/.dsh/sessions/<工作目录编码>/<session-id>/session.v4.jsonl.zstd
文件是「zstd 流式压缩」的 JSONL（每行一个 JSON 事件，type 字段区分类型）。

依赖：zstandard（pip install zstandard），跨平台。

用法：
    python dsh_bridge.py list                 列出所有会话（标题/时间/id/cwd）
    python dsh_bridge.py latest [N]           最近 N 个会话（默认 5）
    python dsh_bridge.py dump <id|序号|关键词> 导出某会话的完整对话
    python dsh_bridge.py tail <id|序号> [N]   看某会话最后 N 条消息（默认 10）
"""
import sys
import os
import io
import json
import glob
import argparse
from datetime import datetime

try:
    import zstandard
except ImportError:
    sys.stderr.write(
        "缺少 zstandard 模块。请安装：\n"
        "  pip install zstandard\n"
        "或直接使用 DSH 自带的 python（已装好）：\n"
        "  C:\\Users\\15247\\.dsh\\dsh-runtimes\\dsh-primary-runtime\\dependencies\\python\\python.exe\n"
    )
    sys.exit(1)

DSH_DIR = os.path.expanduser("~/.dsh")
SESSIONS_GLOB = os.path.join(DSH_DIR, "sessions", "*", "*", "session.v4.jsonl.zstd")

# user/message 里这些前缀是 DSH 注入的系统内容，不是用户真实输入
_SYSTEM_PREFIXES = (
    "<system-reminder>",
    "Time sampled while preparing",
    "The user changed the approval policy",
    "The user changed the sandbox mode",
)


def _fmt_ts(ms):
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ms)


def _decompress(path):
    """zstd 流式解压（跨帧），返回行列表。"""
    raw = open(path, "rb").read()
    dctx = zstandard.ZstdDecompressor()
    with dctx.stream_reader(io.BytesIO(raw), read_across_frames=True) as r:
        out = r.read()
    return out.decode("utf-8", errors="replace").splitlines()


def _parse_lines(lines):
    events = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            events.append(json.loads(ln))
        except Exception:
            continue
    return events


def _read_meta(path):
    """只取会话头 + 标题（避免完整解压的开销时可复用，这里直接解压）。"""
    events = _parse_lines(_decompress(path))
    meta = {"id": None, "cwd": None, "created_at": None, "title": None, "path": path}
    for e in events:
        t = e.get("type")
        if t == "session":
            meta["id"] = e.get("id")
            meta["cwd"] = e.get("cwd")
            meta["created_at"] = e.get("createdAt")
        elif t == "session/title":
            meta["title"] = e.get("data", {}).get("title")
    return meta


def find_sessions():
    """枚举所有会话文件，返回 meta 列表（按创建时间倒序）。"""
    sessions = []
    for path in glob.glob(SESSIONS_GLOB):
        try:
            meta = _read_meta(path)
            sessions.append(meta)
        except Exception:
            continue
    sessions.sort(key=lambda m: m["created_at"] or 0, reverse=True)
    return sessions


# ---------- 事件渲染 ----------

def _is_real_user(text):
    return not text.startswith(_SYSTEM_PREFIXES)


def _render_content(content, prefix="  "):
    """渲染 assistant message 的 content 列表（reasoning/tool-call/text）。"""
    parts = []
    for c in content:
        kind = c.get("type")
        if kind == "reasoning":
            parts.append(f"{prefix}💭 思考：{c.get('text','').strip()}")
        elif kind == "tool-call":
            args = c.get("arguments", "")
            try:
                a = json.loads(args) if isinstance(args, str) else args
                brief = json.dumps(a, ensure_ascii=False)[:200]
            except Exception:
                brief = str(args)[:200]
            parts.append(f"{prefix}🔧 调用 {c.get('name')}：{brief}")
        elif kind == "text":
            parts.append(f"{prefix}{c.get('text','').strip()}")
        else:
            parts.append(f"{prefix}[{kind}] {json.dumps(c, ensure_ascii=False)[:200]}")
    return "\n".join(parts)


def _render_events(events, show_reasoning=True, max_result=400):
    """把事件列表渲染成可读对话文本。"""
    out = []
    for e in events:
        t = e.get("type")
        d = e.get("data", {})

        if t == "session":
            continue
        elif t == "session/title":
            continue
        elif t == "user/message":
            if d.get("role") != "user":
                continue
            for c in d.get("content", []):
                text = c.get("text", "")
                if c.get("type") == "text" and _is_real_user(text):
                    out.append(f"👤 用户：{text.strip()}")
        elif t == "agent/inbox/spliced":
            for item in d.get("inserted", []):
                if item.get("role") != "user":
                    continue
                for c in item.get("content", []):
                    text = c.get("text", "")
                    if c.get("type") == "text" and _is_real_user(text):
                        # 与 user/message 去重：这里通常早于 user/message，若已出现则跳过
                        pass
        elif t == "assistant/message":
            msg = d.get("message", {})
            content = msg.get("content", [])
            body = []
            for c in content:
                kind = c.get("type")
                if kind == "reasoning":
                    if show_reasoning:
                        body.append(f"  💭 思考：{c.get('text','').strip()}")
                elif kind == "tool-call":
                    try:
                        args = c.get("arguments", "")
                        a = json.loads(args) if isinstance(args, str) else args
                        brief = json.dumps(a, ensure_ascii=False)[:200]
                    except Exception:
                        brief = str(c.get("arguments", ""))[:200]
                    body.append(f"  🔧 调用 {c.get('name')}：{brief}")
                elif kind == "text":
                    body.append(f"  {c.get('text','').strip()}")
            out.append("🤖 DSH：\n" + "\n".join(body))
        elif t == "tool/result":
            msg = d.get("message", {})
            name = msg.get("source", {}).get("callId", "")
            is_err = msg.get("isError", False)
            for c in msg.get("content", []):
                text = c.get("text", "")
                if c.get("type") == "text":
                    txt = text.strip()
                    if len(txt) > max_result:
                        txt = txt[:max_result] + f"...(截断，共 {len(text)} 字符)"
                    tag = "❌ 结果" if is_err else "📋 结果"
                    out.append(f"{tag}：{txt}")
        elif t == "system/message":
            continue  # 系统提示默认不显示
        elif t == "tool/call":
            continue  # 调用意图已在 assistant/message 里渲染
        # 其他事件（turn/start、step/start 等）默认忽略
    return "\n\n".join(out)


# ---------- 命令 ----------

def cmd_list(args):
    sessions = find_sessions()
    if not sessions:
        print("未找到任何 DSH 会话。")
        return
    print(f"共 {len(sessions)} 个会话：\n")
    for i, m in enumerate(sessions):
        title = m["title"] or "(无标题)"
        created = _fmt_ts(m["created_at"]) if m["created_at"] else "?"
        print(f"[{i}] {title}")
        print(f"    id: {m['id']}")
        print(f"    cwd: {m['cwd']}")
        print(f"    时间: {created}")


def cmd_latest(args):
    n = args.n if args.n is not None else 5
    sessions = find_sessions()[:n]
    for i, m in enumerate(sessions):
        title = m["title"] or "(无标题)"
        created = _fmt_ts(m["created_at"]) if m["created_at"] else "?"
        print(f"[{i}] {title}  |  {created}  |  {m['id']}")


def _pick_session(sessions, target):
    """target 可为序号、id 前缀、或标题关键词。"""
    if target is None:
        return sessions[0] if sessions else None
    if target.isdigit():
        idx = int(target)
        if 0 <= idx < len(sessions):
            return sessions[idx]
    for m in sessions:
        if m["id"] and m["id"].startswith(target):
            return m
    for m in sessions:
        if m["title"] and target in m["title"]:
            return m
    return None


def cmd_dump(args):
    sessions = find_sessions()
    m = _pick_session(sessions, args.target)
    if not m:
        print("未找到匹配的会话。先运行 list 查看。")
        return
    events = _parse_lines(_decompress(m["path"]))
    print(f"# {m['title'] or '(无标题)'}")
    print(f"# id: {m['id']}  cwd: {m['cwd']}")
    print(f"# 时间: {_fmt_ts(m['created_at']) if m['created_at'] else '?'}\n")
    print(_render_events(events, show_reasoning=not args.no_reasoning))


def cmd_tail(args):
    sessions = find_sessions()
    m = _pick_session(sessions, args.target)
    if not m:
        print("未找到匹配的会话。先运行 list 查看。")
        return
    n = args.n if args.n is not None else 10
    events = _parse_lines(_decompress(m["path"]))
    # 只取最近 n 条「消息类」事件
    rendered = _render_events(events, show_reasoning=not args.no_reasoning)
    blocks = [b for b in rendered.split("\n\n") if b.strip()]
    for b in blocks[-n:]:
        print(b)
        print()


def main():
    p = argparse.ArgumentParser(description="读取 DeepSeek Harness 对话")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有会话")
    lp = sub.add_parser("latest", help="最近 N 个会话")
    lp.add_argument("n", nargs="?", type=int, default=5)
    dp = sub.add_parser("dump", help="导出某会话完整对话")
    dp.add_argument("target", nargs="?", default=None, help="序号/会话id前缀/标题关键词")
    dp.add_argument("--no-reasoning", action="store_true", help="隐藏思考内容")
    tp = sub.add_parser("tail", help="看某会话最后 N 条消息")
    tp.add_argument("target", nargs="?", default=None, help="序号/会话id前缀/标题关键词")
    tp.add_argument("n", nargs="?", type=int, default=10)
    tp.add_argument("--no-reasoning", action="store_true", help="隐藏思考内容")

    args = p.parse_args()
    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "latest":
        cmd_latest(args)
    elif args.cmd == "dump":
        cmd_dump(args)
    elif args.cmd == "tail":
        cmd_tail(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
