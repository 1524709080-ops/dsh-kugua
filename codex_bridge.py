#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
codex_bridge.py —— 读取 OpenAI Codex 本地对话历史，供 DSH agent 接收。

数据来源（新版 codex 的 "paginated" 存储）：
  1. ~/.codex/state_5.sqlite   -> threads 表（会话元数据 + rollout_path）
  2. rollout jsonl 文件        -> 权威、实时写入的逐条事件（对话正文在这）
  3. ~/.codex/thread_history_1.sqlite -> thread_items 表（旧会话兜底）

用法：
  python codex_bridge.py list                列出所有会话
  python codex_bridge.py latest [N]          最近更新的 N 个会话概要（默认 5）
  python codex_bridge.py dump <id|关键词>     导出某会话完整对话（按时间排序）
  python codex_bridge.py tail <id|关键词> [N] 只输出某会话最近 N 条消息（默认 20）
"""

import os
import sys
import io
import json
import sqlite3
import datetime
import argparse

try:
    import zstandard
except ImportError:  # zstandard 可选；读取 .zst 时才需要
    zstandard = None

BASE = os.path.join(os.path.expanduser("~"), ".codex")
STATE_DB = os.path.join(BASE, "state_5.sqlite")
HIST_DB = os.path.join(BASE, "thread_history_1.sqlite")


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _ts(ms):
    if not ms:
        return "?"
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ms)


def _ts_iso(s):
    """rollout 行顶层的 ISO timestamp -> 本地时间字符串"""
    if not s:
        return "?"
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return s


def _clean_user(text):
    """去掉 codex 注入的 'Files mentioned / My request' 前缀，保留真实用户输入"""
    t = text.strip()
    if not t:
        return ""
    # 真实用户输入在 '## My request:' 之后
    mark = "## My request:"
    if mark in t:
        return t.split(mark, 1)[1].strip()
    return t


def list_threads():
    con = _ro(STATE_DB)
    rows = con.execute(
        "SELECT id, title, COALESCE(updated_at_ms, updated_at) AS u, model, cwd, rollout_path "
        "FROM threads ORDER BY COALESCE(updated_at_ms, updated_at) DESC"
    ).fetchall()
    con.close()
    return rows


def find_thread(keyword):
    rows = list_threads()
    for r in rows:
        if r[0] == keyword:
            return r
    # 按序号匹配（"2" 之类）
    if keyword.isdigit():
        idx = int(keyword) - 1
        if 0 <= idx < len(rows):
            return rows[idx]
    # 标题/首条消息模糊匹配
    hits = [r for r in rows if keyword.lower() in (r[1] or "").lower()]
    return hits[0] if hits else None


# ---------- rollout jsonl 解析 ----------

def _read_lines(path):
    """读取 jsonl 文件为行列表；.zst 后缀自动走 zstd 解压（跨帧）。"""
    if path.endswith(".zst"):
        if zstandard is None:
            raise RuntimeError("读取 .zst 需要 zstandard：pip install zstandard")
        raw = open(path, "rb").read()
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(io.BytesIO(raw), read_across_frames=True) as r:
            data = r.read()
        return data.decode("utf-8", errors="replace").splitlines()
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def _parse_rollout(path):
    """解析 rollout jsonl，返回 [(ts, role, text), ...]，role: user/assistant/reasoning/tool"""
    out = []
    try:
        lines = _read_lines(path)
    except Exception:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = _ts_iso(d.get("timestamp"))
        p = d.get("payload")
        if not isinstance(p, dict):
            continue
        kind = d.get("type")
        pt = p.get("type")

        if kind == "response_item" and pt == "message":
            role = p.get("role")
            kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds", [])
            # 过滤环境/系统注入
            if role == "developer":
                continue
            texts = []
            for c in p.get("content", []):
                if isinstance(c, dict) and c.get("text"):
                    texts.append(c["text"])
            txt = "\n".join(texts).strip()
            if not txt:
                continue
            if role == "user":
                if "environments.environment_context" in kinds:
                    continue
                if txt.strip().startswith("<environment_context"):
                    continue
                txt = _clean_user(txt)
                if not txt:
                    continue
                out.append((ts, "user", txt))
            elif role == "assistant":
                out.append((ts, "assistant", txt))
        elif kind == "response_item" and pt == "reasoning":
            s = p.get("summary") or []
            parts = []
            for x in s:
                if isinstance(x, dict) and x.get("text"):
                    parts.append(x["text"])
                elif isinstance(x, str):
                    parts.append(x)
            if parts:
                out.append((ts, "reasoning", " ".join(parts)))
            elif p.get("content"):
                out.append((ts, "reasoning", "[思考]（内容块，未展开）"))
        elif kind == "response_item" and pt in ("function_call", "custom_tool_call"):
            name = p.get("name") or p.get("tool") or "?"
            args = p.get("arguments") or ""
            if isinstance(args, (dict, list)):
                args = json.dumps(args, ensure_ascii=False)[:200]
            out.append((ts, "tool", "[工具] %s(%s)" % (name, str(args)[:200])))
    return out


def _parse_thread_items(thread_id):
    """旧会话兜底：从 thread_history_1.sqlite 读"""
    out = []
    con = _ro(HIST_DB)
    rows = con.execute(
        "SELECT item_type, item_json, created_at_ms FROM thread_items "
        "WHERE thread_id=? ORDER BY created_at_ms ASC",
        (thread_id,),
    ).fetchall()
    con.close()
    for itype, jtxt, ms in rows:
        try:
            d = json.loads(jtxt)
        except Exception:
            continue
        ts = _ts(ms)
        if itype == "userMessage":
            parts = []
            for c in d.get("content", []):
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text", ""))
                elif isinstance(c, dict) and c.get("type") == "localImage":
                    parts.append("[图片附件]")
            txt = _clean_user("\n".join(parts))
            if txt:
                out.append((ts, "user", txt))
        elif itype == "agentMessage":
            txt = (d.get("text") or "").strip()
            if txt:
                out.append((ts, "assistant", txt))
        elif itype == "reasoning":
            s = d.get("summary") or []
            parts = []
            for x in s:
                if isinstance(x, dict) and x.get("text"):
                    parts.append(x["text"])
                elif isinstance(x, str):
                    parts.append(x)
            if parts:
                out.append((ts, "reasoning", " ".join(parts)))
        elif itype == "commandExecution":
            out.append((ts, "tool", "[命令] " + (d.get("command") or "")[:200]))
        elif itype in ("function_call", "mcpToolCall"):
            out.append((ts, "tool", "[工具] %s" % (d.get("name") or d.get("tool") or "?")))
        elif itype == "webSearch":
            out.append((ts, "tool", "[搜索] " + str(d.get("query") or "")[:200]))
        elif itype == "fileChange":
            chs = d.get("changes") or []
            paths = [c.get("path", "?") for c in chs if isinstance(c, dict)]
            out.append((ts, "tool", "[文件修改] " + ", ".join(paths[:5])))
    return out


def load_conversation(thread):
    """thread = (id, title, updated, model, cwd, rollout_path) -> [(ts, role, text), ...]"""
    tid = thread[0]
    rollout = thread[5]
    if rollout:
        path = rollout.replace("\\\\?\\", "")
        for cand in (path, path + ".zst"):
            if os.path.exists(cand):
                items = _parse_rollout(cand)
                if items:
                    return items
    return _parse_thread_items(tid)


def _render(items):
    marks = {"user": "👤 用户", "assistant": "🤖 Codex", "reasoning": "💭 思考", "tool": "🔧"}
    out = []
    for ts, role, text in items:
        if role in ("user", "assistant"):
            out.append("%s  [%s]\n%s" % (marks[role], ts, text))
        else:
            out.append("%s %s  [%s] %s" % (marks[role], role, ts, text))
    return out


def cmd_list(_args):
    rows = list_threads()
    if not rows:
        print("(未找到任何 codex 会话)")
        return
    print("共 %d 个会话（按最近更新排序）：\n" % len(rows))
    for i, r in enumerate(rows, 1):
        tid, title, u, model, cwd, rollout = r
        print("%2d. [%s] %s" % (i, _ts(u), _clean_user(title or "(无标题)")[:60]))
        print("    id=%s  model=%s" % (tid, model or "?"))
    print("\n用 `dump <序号|id|标题关键词>` 查看某个会话。")


def cmd_latest(args):
    rows = list_threads()
    n = args.n or 5
    for r in rows[:n]:
        tid, title, u, model, cwd, rollout = r
        print("=" * 70)
        print("[%s] %s" % (_ts(u), (title or "(无标题)")[:70]))
        print("id=%s  model=%s" % (tid, model or "?"))
        items = load_conversation(r)
        msgs = [x for x in _render(items) if x.startswith(("👤", "🤖"))]
        for m in msgs[-4:]:
            print("  " + m.replace("\n", "\n  ")[:500])
    print("=" * 70)


def cmd_dump(args):
    t = find_thread(args.target)
    if not t:
        print("未找到会话：%s" % args.target)
        print("用 `list` 查看可用会话。")
        return
    tid, title, u, model, cwd, rollout = t
    print("# %s" % _clean_user(title or "(无标题)"))
    print("# id=%s  model=%s  更新时间=%s\n" % (tid, model or "?", _ts(u)))
    for line in _render(load_conversation(t)):
        print(line)
        print()


def cmd_tail(args):
    t = find_thread(args.target)
    if not t:
        print("未找到会话：%s" % args.target)
        return
    tid, title, u, model, cwd, rollout = t
    n = args.n or 20
    print("# %s  (id=%s)\n" % (_clean_user(title or "(无标题)"), tid))
    for line in _render(load_conversation(t))[-n:]:
        print(line)
        print()


def main():
    p = argparse.ArgumentParser(description="读取 codex 本地对话历史")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有会话")

    pl = sub.add_parser("latest", help="最近更新会话的概要")
    pl.add_argument("n", nargs="?", type=int, default=5)

    pd = sub.add_parser("dump", help="导出某个会话完整对话")
    pd.add_argument("target")

    pt = sub.add_parser("tail", help="某会话最近 N 条消息")
    pt.add_argument("target")
    pt.add_argument("n", nargs="?", type=int, default=20)

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
    sys.stdout.reconfigure(encoding="utf-8")
    main()
