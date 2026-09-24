"""AI 外脑 Demo —— Streamlit 单页面应用。

运行：streamlit run app.py
"""
import os
import time

import streamlit as st

import db
import services

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")

db.init_db()

st.set_page_config(page_title="AI 外脑 Demo", page_icon="🧠", layout="wide")
st.title("AI 外脑 Demo")
st.caption("对话 · PDF 背景知识 · 会议风险分析 · 长记忆 · 联网搜索 · PPT 生成")

# ======================= 侧边栏：上传 =======================

with st.sidebar:
    st.header("背景知识")
    pdf_file = st.file_uploader("上传 PDF", type=["pdf"], key="pdf_uploader")
    st.header("会议记录")
    txt_file = st.file_uploader("上传 TXT 会议原文", type=["txt"], key="txt_uploader")

if pdf_file is not None:
    pdf_key = f"pdf_{pdf_file.name}_{pdf_file.size}"
    if st.session_state.get("pdf_processed") != pdf_key:
        with st.spinner("正在解析 PDF..."):
            try:
                content = services.extract_pdf_text(pdf_file.getvalue())
                if not content.strip():
                    st.session_state["pdf_processed"] = pdf_key
                    st.session_state["pdf_error"] = "PDF 解析失败：未提取到文本（扫描版 PDF 不支持 OCR）"
                else:
                    db.add_pdf(pdf_file.name, content)
                    st.session_state["pdf_processed"] = pdf_key
                    st.session_state.pop("pdf_error", None)
                    st.sidebar.success(f"已保存《{pdf_file.name}》（{len(content)} 字），可作为背景知识")
            except Exception as e:  # noqa: BLE001
                st.session_state["pdf_processed"] = pdf_key
                st.session_state["pdf_error"] = f"PDF 解析失败：{e}"
    else:
        st.sidebar.success(f"已加载《{pdf_file.name}》")

if "pdf_error" in st.session_state:
    st.sidebar.error(st.session_state["pdf_error"])

if txt_file is not None:
    txt_key = f"meeting_{txt_file.name}_{txt_file.size}"
    if st.session_state.get("meeting_processed") != txt_key:
        text = txt_file.read().decode("utf-8", errors="ignore").strip()
        if not text:
            st.session_state["meeting_processed"] = txt_key
            st.session_state["meeting_error"] = "TXT 内容为空，请重新上传"
        else:
            with st.spinner("正在分析会议..."):
                try:
                    analysis = services.analyze_meeting(text)
                except Exception as e:  # noqa: BLE001
                    analysis = None
                    st.session_state["meeting_processed"] = txt_key
                    st.session_state["meeting_error"] = f"会议分析失败：{e}"
            if analysis is not None:
                st.session_state.pop("meeting_error", None)
                db.add_meeting(txt_file.name, analysis)
                services.save_meeting_memories(analysis, source="meeting")
                email_status = "无风险，无需发送邮件"
                if analysis["risks"]:
                    if services.is_smtp_configured():
                        ok, msg = services.send_risk_email(
                            analysis["summary"], analysis["risks"], analysis["todos"]
                        )
                        email_status = msg if ok else f"{msg}（详见下方邮件状态）"
                    else:
                        email_status = "检测到风险，但邮件配置未完成"
                st.session_state["meeting_analysis"] = {
                    "filename": txt_file.name,
                    "analysis": analysis,
                    "email_status": email_status,
                }
                st.session_state["meeting_processed"] = txt_key
                st.sidebar.success(f"会议《{txt_file.name}》分析完成")

if "meeting_error" in st.session_state:
    st.error(st.session_state["meeting_error"])

# ======================= 会议分析展示 =======================

if "meeting_analysis" in st.session_state:
    ma = st.session_state["meeting_analysis"]
    st.subheader("会议分析")
    st.markdown(f"**摘要：** {ma['analysis']['summary'] or '（未识别）'}")
    if ma["analysis"]["risks"]:
        st.markdown("**风险：**")
        for r in ma["analysis"]["risks"]:
            if isinstance(r, dict):
                reason = f"（原因：{r.get('reason', '')}）" if r.get("reason") else ""
                st.markdown(f"- {r.get('risk', '')}{reason}")
            else:
                st.markdown(f"- {r}")
    else:
        st.info("未发现风险")
    if ma["analysis"]["todos"]:
        st.markdown("**待办事项：**")
        for t in ma["analysis"]["todos"]:
            if isinstance(t, dict):
                owner = t.get("owner", "")
                deadline = t.get("deadline", "")
                st.markdown(f"- [{owner}] {t.get('task', '')} - 截止时间 {deadline}")
            else:
                st.markdown(f"- {t}")
    st.markdown(f"**邮件状态：** {ma['email_status']}")
    st.divider()

# ======================= 最近生成的 PPT（下载入口） =======================

if "ppt_info" in st.session_state and os.path.exists(st.session_state["ppt_info"]["path"]):
    info = st.session_state["ppt_info"]
    st.info(f"最近生成的 PPT：《{info['title']}》")
    with open(info["path"], "rb") as fh:
        st.download_button(
            "下载 PPT",
            fh.read(),
            file_name=os.path.basename(info["path"]),
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    st.divider()

# ======================= 聊天区 =======================

st.subheader("对话")
for m in db.get_messages(limit=100):
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

prompt = st.chat_input("输入问题，例如：帮我生成一个 5 页的项目风险汇报 PPT / 查询今天最新的 AI 新闻")


def _save_and_display_user(prompt):
    db.add_message("user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)


if prompt:
    if services.is_ppt_request(prompt):
        _save_and_display_user(prompt)
        with st.spinner("正在生成 PPT..."):
            try:
                context = services.build_context(prompt)
                structure = services.generate_ppt_structure(prompt, context)
                if not structure["slides"]:
                    raise ValueError("LLM 未返回有效的 PPT 结构")
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                path = os.path.join(OUTPUT_DIR, f"PPT_{time.strftime('%Y%m%d_%H%M%S')}.pptx")
                services.build_pptx(structure, path)
                st.session_state["ppt_info"] = {"title": structure["title"], "path": path}
                msg = (
                    f"已根据你的需求生成 PPT：《{structure['title']}》"
                    f"（共 {len(structure['slides'])} 页），点击下方按钮下载。"
                )
                db.add_message("assistant", msg)
                with st.chat_message("assistant"):
                    st.markdown(msg)
                st.success(f"PPT 已生成：{os.path.basename(path)}")
                with open(path, "rb") as fh:
                    st.download_button(
                        "下载 PPT",
                        fh.read(),
                        file_name=os.path.basename(path),
                        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    )
            except Exception as e:  # noqa: BLE001
                st.error(f"PPT 生成失败：{e}")
                with st.chat_message("assistant"):
                    st.markdown(f"PPT 生成失败：{e}")
    else:
        _save_and_display_user(prompt)
        with st.spinner("思考中..."):
            try:
                result = services.chat_reply(prompt)
            except Exception as e:  # noqa: BLE001
                result = {"reply": f"（LLM 调用失败：{e}）", "search_status": "失败"}
        db.add_message("assistant", result["reply"])
        with st.chat_message("assistant"):
            st.markdown(result["reply"])
        if result.get("search_status") in ("成功", "无结果", "失败"):
            st.caption(f"联网搜索：{result['search_status']}")
        # 每轮聊天后抽取长期记忆（失败静默，不影响主流程）
        try:
            services.extract_and_save_memories(prompt, source="chat")
        except Exception:  # noqa: BLE001
            pass
