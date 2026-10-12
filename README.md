# AI 外脑 Demo

一个最小可运行、可演示的 AI 外脑 Demo：对话、PDF 背景知识、会议风险分析 + 邮件、长记忆、联网搜索、PPT 生成。

技术栈：Streamlit + OpenAI 兼容 LLM API + PyMuPDF + SQLite + ddgs（联网搜索）+ python-pptx + smtplib + Tesseract OCR（扫描版 PDF）。

## 安装

```bash
pip install -r requirements.txt
```

## 配置

```bash
cp .env.example .env
```

然后填写 `.env`：

| 变量 | 说明 |
| --- | --- |
| `OPENAI_API_KEY` | LLM API Key（OpenAI 兼容接口均可用） |
| `OPENAI_BASE_URL` | 可选，兼容服务的 Base URL，例如 `https://api.deepseek.com/v1` |
| `OPENAI_MODEL` | 可选，默认 `gpt-4o-mini` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `MAIL_FROM` / `MAIL_TO` | 风险提醒邮件，可选；不配置则页面显示“检测到风险，但邮件配置未完成” |
| `TESSERACT_CMD` | 可选，Tesseract 可执行文件路径；不配置则自动探测 PATH 与常见安装位置 |
| `TESSERACT_LANGS` | 可选，OCR 语言，默认 `chi_sim+eng` |

扫描版 PDF：需本机安装 [Tesseract 5](https://github.com/tesseract-ocr/tesseract) 及中文语言包 `chi_sim`（`tesseract --list-langs` 可检查）。上传扫描版 PDF 时自动逐页 OCR 识别。

## 运行

```bash
streamlit run app.py
```

浏览器打开终端提示的地址（默认 http://localhost:8501）。

## 6 个测试点

1. **普通聊天**：输入“你好，你是谁？”，AI 正常回复，消息进入历史，刷新页面后仍在。
2. **PDF 背景知识**：侧边栏上传 PDF，然后问 PDF 中明确存在的信息，回答应结合 PDF 内容。
3. **会议 TXT + 风险 + 邮件**：侧边栏上传会议 TXT（含延期风险类内容），页面展示摘要/风险/待办；有风险时按 SMTP 配置发送邮件（未配置则提示未配置），程序不崩溃。
4. **长记忆**：上传会议 TXT 后问“谁负责支付模块的问题？”、问“团队什么时候在哪里复盘？”；再聊天说“我下个月和王强在杭州讨论 Apollo 项目”，刷新后问“我之前准备和谁在杭州讨论什么项目？”，都能答出。
5. **联网搜索**：输入“查询今天最新的 AI 新闻”，应实际联网搜索并基于结果回答（消息中含“今天/最新/新闻/查询”等词触发）。
6. **生成 PPT**：输入“根据刚才的会议内容，生成一个 5 页的项目风险汇报 PPT”，生成真实 `.pptx` 并出现下载按钮。

数据（聊天历史、长期记忆、PDF、会议分析）保存在项目目录下的 `brain.db`；生成的 PPT 在 `outputs/` 目录。
