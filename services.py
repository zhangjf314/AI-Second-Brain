"""AI 外脑 Demo 核心服务。

统一封装：
- call_llm：OpenAI-compatible LLM 调用（唯一的 LLM 入口）
- 联网搜索（ddgs，无需额外 API Key）
- PDF 文本提取（PyMuPDF）
- 会议分析（LLM 结构化抽取）
- 聊天内容记忆抽取（LLM 结构化抽取）
- 风险邮件（smtplib）
- PPT 结构生成 + python-pptx 生成
- 聊天上下文组装（长期记忆 + PDF 背景 + 搜索结果）
"""
import json
import os
import re
import shutil
import smtplib
from email.header import Header
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

# ======================= LLM 封装 =======================

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
        _client = OpenAI(api_key=api_key or "not-set", base_url=base_url)
    return _client


def get_model():
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def call_llm(messages, response_format=None):
    """调用 OpenAI-compatible LLM。

    response_format 可选，如 {"type": "json_object"}；
    部分兼容服务不支持该参数，失败时自动去掉重试一次。
    """
    client = _get_client()
    kwargs = {"model": get_model(), "messages": messages}
    if response_format is not None:
        kwargs["response_format"] = response_format
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception:
        if response_format is not None:
            kwargs.pop("response_format", None)
            resp = client.chat.completions.create(**kwargs)
        else:
            raise
    return resp.choices[0].message.content


def parse_json(text):
    """把 LLM 输出解析为 dict/list；容忍 markdown 代码块等包装。"""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


# ======================= 联网搜索 =======================

SEARCH_KEYWORDS = [
    "今天", "当前", "现在", "最近", "最新", "新闻",
    "实时", "目前", "查询", "热搜", "行情", "天气",
]


def should_search(text):
    """关键词简单判断是否需要联网搜索。"""
    return any(k in text for k in SEARCH_KEYWORDS)


def web_search(query, max_results=5):
    """使用 ddgs（DuckDuckGo，无需 API Key）联网搜索，返回标题/URL/摘要列表。"""
    from ddgs import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append(
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "body": r.get("body", ""),
                }
            )
    return results


def format_search_results(query, results):
    lines = [f"【联网搜索结果】（搜索词：{query}）"]
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. {r['title']}\n   URL: {r['url']}\n   摘要: {r['body']}"
        )
    return "\n".join(lines)


# ======================= PDF 背景知识 =======================

TESSERACT_LANGS = os.getenv("TESSERACT_LANGS", "chi_sim+eng").strip() or "chi_sim+eng"


def tesseract_cmd():
    """定位 tesseract.exe：优先 TESSERACT_CMD 环境变量，其次 PATH，最后常见安装位置。"""
    cmd = os.getenv("TESSERACT_CMD", "").strip()
    if cmd:
        return cmd
    found = shutil.which("tesseract")
    if found:
        return found
    for p in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if os.path.exists(p):
            return p
    return "tesseract"


def _ocr_pdf(file_bytes):
    """用 Tesseract OCR 解析无文本层的 PDF（扫描件）。

    每页渲染为图片后逐页识别；需要本机安装 Tesseract 5 及对应语言包
    （中文 chi_sim / 英文 eng），路径见 tesseract_cmd()。
    """
    import io

    import pytesseract
    from PIL import Image

    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd()
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        texts = []
        for page in doc:
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(img, lang=TESSERACT_LANGS)
            texts.append(text)
    finally:
        doc.close()
    return "\n".join(t for t in texts if t.strip()).strip()


def extract_pdf_text(file_bytes):
    """提取 PDF 文本：优先文本层（PyMuPDF），无文本层（扫描件）自动走 Tesseract OCR。"""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        text = "\n".join(page.get_text() for page in doc).strip()
    finally:
        doc.close()
    if len(text) < 20:
        # 几乎没有文本层，视为扫描件，回退 OCR
        return _ocr_pdf(file_bytes)
    return text


def pdf_context_for_query(pdf_content, query, max_len=8000):
    """超长 PDF 的简单处理：优先关键词命中段落，否则截断开头。"""
    if len(pdf_content) <= max_len:
        return pdf_content
    words = [w for w in re.split(r"[\W_]+", query) if len(w) >= 2]
    segments = [s for s in re.split(r"\n+", pdf_content) if s.strip()]
    hits = [s for s in segments if any(w in s for w in words)]
    if hits:
        chosen = "\n".join(hits)
        return chosen if len(chosen) <= max_len else chosen[:max_len]
    return pdf_content[:max_len]


# ======================= 会议分析 =======================

MEETING_SYSTEM_PROMPT = """你是一个会议分析助手。分析会议原文，只输出严格 JSON（不要输出 JSON 以外的任何内容）：
{
  "summary": "会议摘要",
  "people": ["张三", "李四"],
  "time": ["下周五"],
  "location": ["上海办公室"],
  "topics": ["项目上线", "数据迁移"],
  "risks": [{"risk": "风险描述", "reason": "原因"}],
  "todos": [{"task": "待办事项", "owner": "负责人", "deadline": "截止时间"}]
}
字段无法确定时允许为空数组或空字符串。"""


def analyze_meeting(text):
    """分析会议原文，返回结构化结果；LLM 失败时抛异常由调用方处理。"""
    resp = call_llm(
        [
            {"role": "system", "content": MEETING_SYSTEM_PROMPT},
            {"role": "user", "content": f"会议原文：\n{text[:12000]}"},
        ],
        response_format={"type": "json_object"},
    )
    data = parse_json(resp) or {}
    return {
        "summary": str(data.get("summary", "") or ""),
        "people": _as_list(data.get("people")),
        "time": _as_list(data.get("time")),
        "location": _as_list(data.get("location")),
        "topics": _as_list(data.get("topics")),
        "risks": _as_list(data.get("risks")) if isinstance(data.get("risks"), list) else [],
        "todos": _as_list(data.get("todos")) if isinstance(data.get("todos"), list) else [],
    }


def save_meeting_memories(analysis, source="meeting"):
    """把会议分析结果中的 人物/时间/地点/主题 写入长期记忆。"""
    from db import add_memory

    context = (analysis.get("summary") or "")[:100]
    for p in analysis.get("people", []):
        add_memory("person", p, context, source)
    for t in analysis.get("time", []):
        add_memory("time", t, context, source)
    for loc in analysis.get("location", []):
        add_memory("location", loc, context, source)
    for topic in analysis.get("topics", []):
        add_memory("topic", topic, context, source)


# ======================= 聊天内容记忆抽取 =======================

MEMORY_EXTRACT_PROMPT = """从下面的用户消息中抽取长期记忆。只抽取消息中明确提到的信息，没有则为空数组。
只输出严格 JSON（不要输出 JSON 以外的任何内容）：{"people": [], "times": [], "locations": [], "topics": []}"""


def extract_memories(text):
    resp = call_llm(
        [
            {"role": "system", "content": MEMORY_EXTRACT_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
    )
    data = parse_json(resp) or {}
    return {
        "people": _as_list(data.get("people")),
        "times": _as_list(data.get("times")),
        "locations": _as_list(data.get("locations")),
        "topics": _as_list(data.get("topics")),
    }


def extract_and_save_memories(user_text, source="chat"):
    """每轮聊天后抽取并写入长期记忆（失败静默，不影响主流程）。"""
    from db import add_memory

    data = extract_memories(user_text)
    context = user_text[:100]
    for p in data["people"]:
        add_memory("person", p, context, source)
    for t in data["times"]:
        add_memory("time", t, context, source)
    for loc in data["locations"]:
        add_memory("location", loc, context, source)
    for topic in data["topics"]:
        add_memory("topic", topic, context, source)


# ======================= 风险邮件 =======================

def smtp_config():
    return {
        "host": os.getenv("SMTP_HOST", "").strip(),
        "port": int(os.getenv("SMTP_PORT", "465").strip() or 465),
        "username": os.getenv("SMTP_USERNAME", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", "").strip(),
        "mail_from": os.getenv("MAIL_FROM", "").strip(),
        "mail_to": os.getenv("MAIL_TO", "").strip(),
    }


def is_smtp_configured():
    cfg = smtp_config()
    return bool(cfg["host"] and cfg["mail_to"])


def build_risk_email_body(summary, risks, todos):
    lines = ["会议风险提醒", f"会议摘要：{summary}", "发现风险："]
    for i, r in enumerate(risks, 1):
        if isinstance(r, dict):
            risk = r.get("risk", "")
            reason = r.get("reason", "")
            lines.append(f"{i}. {risk}" + (f"（原因：{reason}）" if reason else ""))
        else:
            lines.append(f"{i}. {r}")
    lines.append("待办事项：")
    for i, t in enumerate(todos, 1):
        if isinstance(t, dict):
            task = t.get("task", "")
            owner = t.get("owner", "")
            deadline = t.get("deadline", "")
            lines.append(f"{i}. [{owner}] {task} - 截止时间 {deadline}")
        else:
            lines.append(f"{i}. {t}")
    return "\n".join(lines)


def send_risk_email(summary, risks, todos):
    """发送风险提醒邮件。返回 (ok, message)，不抛异常。"""
    cfg = smtp_config()
    body = build_risk_email_body(summary, risks, todos)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header("会议风险提醒", "utf-8")
    msg["From"] = cfg["mail_from"] or cfg["username"]
    msg["To"] = cfg["mail_to"]
    try:
        if cfg["port"] == 465:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
            server.starttls()
        try:
            if cfg["username"]:
                server.login(cfg["username"], cfg["password"])
            server.sendmail(
                cfg["mail_from"] or cfg["username"], [cfg["mail_to"]], msg.as_string()
            )
        finally:
            server.quit()
        return True, "邮件发送成功"
    except Exception as e:  # noqa: BLE001 - 任何失败都返回给页面展示，不崩溃
        return False, f"邮件发送失败：{e}"


# ======================= PPT 生成 =======================

PPT_TRIGGER_RE = re.compile(
    r"(生成|制作|做|创建|写|帮我).{0,15}(ppt|pptx|演示文稿|幻灯片)"
    r"|(ppt|pptx|演示文稿|幻灯片).{0,15}(生成|制作|做|创建|写)",
    re.IGNORECASE,
)


def is_ppt_request(text):
    return bool(PPT_TRIGGER_RE.search(text))


PPT_STRUCTURE_PROMPT = """你是 PPT 结构设计师。根据用户需求生成 PPT 结构，只输出严格 JSON（不要输出 JSON 以外的任何内容）：
{"title": "演示标题", "slides": [{"title": "页面标题", "bullets": ["要点1", "要点2"]}]}
要点使用简洁短语；页数与用户要求一致（未指定时 5 页左右）。"""


def generate_ppt_structure(user_text, context):
    """让 LLM 生成 PPT 结构。"""
    resp = call_llm(
        [
            {"role": "system", "content": PPT_STRUCTURE_PROMPT},
            {"role": "user", "content": f"用户需求：{user_text}\n\n可用上下文：\n{context}"},
        ],
        response_format={"type": "json_object"},
    )
    data = parse_json(resp) or {}
    slides = [s for s in data.get("slides", []) if isinstance(s, dict)]
    return {"title": str(data.get("title") or "演示文稿"), "slides": slides}


def build_pptx(structure, path):
    """用 python-pptx 生成 .pptx：封面 + 每页标题 + bullet 文本。"""
    from pptx import Presentation

    prs = Presentation()
    # 封面页
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    if cover.shapes.title is not None:
        cover.shapes.title.text = structure["title"]
    # 内容页
    for s in structure.get("slides", []):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        if slide.shapes.title is not None:
            slide.shapes.title.text = s.get("title", "")
        body = None
        for ph in slide.placeholders:
            if ph.placeholder_format.idx != 0 and ph.has_text_frame:
                body = ph
                break
        if body is not None:
            tf = body.text_frame
            tf.text = ""
            for i, b in enumerate(s.get("bullets", [])):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.text = str(b)
                p.level = 0
    prs.save(path)
    return path


# ======================= 聊天上下文组装 =======================

CHAT_SYSTEM = """你是一个“AI 外脑”助手。请用与用户相同的语言回答。
规则：
1. 优先使用提供的【长期记忆】【背景知识(PDF)】【最近会议摘要】【联网搜索结果】等上下文回答问题；
2. 回答时效性问题时，以联网搜索结果为准，并在回答末尾列出来源链接；
3. 信息不在上下文中时诚实说明，不要编造。"""


def build_context(user_text):
    """组装长期记忆 + PDF 背景知识 + 最近会议摘要。"""
    from db import get_latest_meeting, get_latest_pdf, get_memories_grouped

    parts = []
    mem = get_memories_grouped()
    if any(mem.values()):
        lines = ["长期记忆："]
        labels = {"person": "人物", "time": "时间", "location": "地点", "topic": "主题"}
        for t, items in mem.items():
            for it in items:
                line = f"- {it['value']}"
                if it.get("context"):
                    line += f"（{it['context']}）"
                lines.append(line)
        parts.append("\n".join(lines))

    pdf = get_latest_pdf()
    if pdf:
        parts.append(
            f"背景知识(PDF)：《{pdf['filename']}》\n"
            + pdf_context_for_query(pdf["content"], user_text)
        )

    meeting = get_latest_meeting()
    if meeting and meeting.get("summary"):
        parts.append("最近会议摘要：" + meeting["summary"])

    return "\n\n".join(parts)


def chat_reply(user_text):
    """处理一轮普通聊天：搜索 + 上下文组装 + LLM 调用。"""
    from db import get_messages

    context = build_context(user_text)
    search_status = "未触发"
    if should_search(user_text):
        try:
            results = web_search(user_text)
            if results:
                context += "\n\n" + format_search_results(user_text, results)
                search_status = "成功"
            else:
                search_status = "无结果"
        except Exception:  # noqa: BLE001 - 搜索失败不阻塞聊天
            search_status = "失败"
        if search_status in ("失败", "无结果"):
            context += "\n\n【联网搜索未返回可用结果，请基于自身知识回答，并说明未能联网核实】"

    system = CHAT_SYSTEM + "\n\n【上下文】\n" + context
    messages = [{"role": "system", "content": system}]
    for m in get_messages(limit=30):
        messages.append({"role": m["role"], "content": m["content"]})

    reply = call_llm(messages)
    return {"reply": reply, "search_status": search_status}
