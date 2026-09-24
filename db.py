"""SQLite 持久化：聊天历史、长期记忆、PDF 背景知识、会议分析结果。

最小实现，仅使用标准库 sqlite3。
"""
import json
import os
import sqlite3

# 数据库文件默认放在项目目录；测试时可通过环境变量 AI_WAI_NAO_DB 覆盖。
DB_PATH = os.environ.get(
    "AI_WAI_NAO_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain.db"),
)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                value TEXT NOT NULL,
                context TEXT DEFAULT '',
                source TEXT DEFAULT 'chat',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS pdfs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                summary TEXT DEFAULT '',
                risks TEXT DEFAULT '[]',
                todos TEXT DEFAULT '[]',
                analysis TEXT DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            """
        )


# ---------- 聊天历史 ----------

def add_message(role, content):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (role, content) VALUES (?, ?)", (role, content)
        )
        return cur.lastrowid


def get_messages(limit=100):
    """返回按时间正序的最新 limit 条消息。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {"role": r["role"], "content": r["content"]} for r in reversed(rows)
    ]


# ---------- 长期记忆 ----------

def add_memory(type_, value, context="", source="chat"):
    """写入一条记忆，简单去重：相同 type+value 不再重复写入。"""
    value = str(value).strip()
    if not value:
        return
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT 1 FROM memories WHERE type = ? AND value = ? LIMIT 1",
            (type_, value),
        ).fetchone()
        if exists:
            return
        conn.execute(
            "INSERT INTO memories (type, value, context, source) VALUES (?, ?, ?, ?)",
            (type_, value, context, source),
        )


def get_memories():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT type, value, context, source FROM memories ORDER BY id"
        ).fetchall()
    return [
        {"type": r["type"], "value": r["value"], "context": r["context"], "source": r["source"]}
        for r in rows
    ]


def get_memories_grouped():
    mem = {"person": [], "time": [], "location": [], "topic": []}
    for m in get_memories():
        mem.setdefault(m["type"], []).append(m)
    return mem


# ---------- PDF 背景知识 ----------

def add_pdf(filename, content):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pdfs (filename, content) VALUES (?, ?)", (filename, content)
        )


def get_latest_pdf():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT filename, content FROM pdfs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {"filename": row["filename"], "content": row["content"]}


# ---------- 会议分析 ----------

def add_meeting(filename, analysis):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO meetings (filename, summary, risks, todos, analysis) VALUES (?, ?, ?, ?, ?)",
            (
                filename,
                analysis.get("summary", ""),
                json.dumps(analysis.get("risks", []), ensure_ascii=False),
                json.dumps(analysis.get("todos", []), ensure_ascii=False),
                json.dumps(analysis, ensure_ascii=False),
            ),
        )


def get_latest_meeting():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT summary, risks, todos, analysis FROM meetings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {
        "summary": row["summary"],
        "risks": json.loads(row["risks"] or "[]"),
        "todos": json.loads(row["todos"] or "[]"),
    }
