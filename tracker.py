# -*- coding: utf-8 -*-
"""
面试/测评邮件追踪器

流程:
  IMAP 拉取新邮件 -> 关键词预筛 -> DeepSeek 抽取结构化信息
  -> 存入 state.json -> 生成汇总报表 -> 到期前 3 天/1 天经 PushPlus 推送到微信

用法:
  python tracker.py check        # 拉邮件 + 抽取 + 报表 + 提醒（定时任务用）
  python tracker.py report       # 只用现有数据重生成报表，不联网抓邮件
  python tracker.py test-push    # 测试 PushPlus 推送
  python tracker.py list         # 列出所有待处理记录
  python tracker.py done <id>    # 标记某条记录为已完成
"""
import argparse
import email
import hashlib
import imaplib
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"

DEFAULT_KEYWORDS = [
    "面试", "测评", "笔试", "AI面", "面试邀请", "笔面试", "能力测评",
    "在线测评", "评估", "测试链接", "视频面试", "面试通知", "性格测试",
]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def load_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as e:
        print(f"[warn] 读取 {path} 失败: {e}")
        return default


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config():
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        print("[error] 缺少 config.json，请复制 config.example.json 并填写邮箱/授权码/推送 token。")
        sys.exit(1)
    cfg.setdefault("keywords", DEFAULT_KEYWORDS)
    cfg.setdefault("remindDaysBefore", [3, 1])
    cfg.setdefault("lookbackDays", 2)
    cfg.setdefault("reportDir", str(BASE_DIR / "reports"))
    cfg.setdefault("pushDailySummary", True)
    cfg.setdefault("pushChannel", "pushplus")
    cfg.setdefault("pushplus", {})
    cfg.setdefault("wxpusher", {})
    cfg.setdefault("serverchan", {})
    return cfg


def parse_dt(s):
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M", "%Y/%m/%d", "%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 邮件解析
# ---------------------------------------------------------------------------
def decode_mime(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def decode_payload(part):
    try:
        data = part.get_payload(decode=True)
        if data is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        try:
            return data.decode(charset, errors="ignore")
        except LookupError:
            return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def get_body(msg):
    text, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp:
                continue
            if ctype == "text/plain":
                text += decode_payload(part)
            elif ctype == "text/html":
                html += decode_payload(part)
    else:
        payload = decode_payload(msg)
        if msg.get_content_type() == "text/html":
            html = payload
        else:
            text = payload
    if not text and html:
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_email_date(raw):
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone().isoformat()
    except Exception:
        return None


def parse_internaldate(meta):
    """从 IMAP FETCH 元数据里解析 INTERNALDATE（服务器接收时间），返回本地 naive datetime。"""
    if not meta:
        return None
    m = re.search(rb'INTERNALDATE "([^"]+)"', meta)
    if not m:
        return None
    raw = m.group(1).decode("ascii", "ignore")
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


def fetch_new_emails(cfg, state):
    imap_cfg = cfg["imap"]
    host = imap_cfg.get("host", "imap.qq.com")
    port = int(imap_cfg.get("port", 993))
    last_uid = int(state.get("last_uid", 0))

    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    M.login(imap_cfg["user"], imap_cfg["authCode"])
    M.select("INBOX")

    # 注意: QQ IMAP 会忽略 SINCE/BEFORE 日期条件，必须用 UID 增量。
    typ, data = M.uid("search", None, "ALL")
    uids = data[0].split() if data and data[0] else []

    cutoff = None
    if last_uid == 0:
        cutoff = datetime.now() - timedelta(days=int(cfg.get("lookbackDays", 2)))

    results = []
    max_uid = last_uid
    for uid_b in uids:
        try:
            uid = int(uid_b)
        except ValueError:
            continue
        if uid <= last_uid:
            continue
        typ, msg_data = M.uid("fetch", uid_b, "(INTERNALDATE RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        meta = msg_data[0][0]
        max_uid = max(max_uid, uid)
        if cutoff is not None:
            internal = parse_internaldate(meta)
            if internal is not None and internal < cutoff:
                continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        subject = decode_mime(msg.get("Subject"))
        sender = decode_mime(msg.get("From"))
        date_hdr = msg.get("Date")
        msg_id = msg.get("Message-ID") or f"{subject}|{date_hdr}"
        results.append({
            "uid": uid,
            "messageId": msg_id,
            "subject": subject,
            "from": sender,
            "date": parse_email_date(date_hdr),
            "body": get_body(msg),
        })
    M.logout()
    state["last_uid"] = max_uid
    return results


def matches_keywords(text, keywords):
    text = text or ""
    return any(k in text for k in keywords)


# ---------------------------------------------------------------------------
# LLM 抽取
# ---------------------------------------------------------------------------
def call_deepseek(cfg, prompt):
    ds = cfg["deepseek"]
    base = ds["apiBase"].rstrip("/")
    url = base + "/chat/completions"
    payload = {
        "model": ds.get("model", "deepseek-chat"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + ds["apiKey"]},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def extract_info(cfg, mail):
    body = (mail["body"] or "")[:3000]
    prompt = (
        "你是一个求职邮件解析助手。请判断下面这封邮件是否与「求职流程」相关"
        "（面试邀请、笔试、在线测评、能力/性格评估、面试安排等），如果相关则提取关键信息。\n\n"
        f"主题: {mail['subject']}\n"
        f"发件人: {mail['from']}\n"
        f"时间: {mail['date']}\n"
        f"正文:\n{body}\n\n"
        "请严格返回 JSON（字段名固定），无法判断的字段填 null（字符串字段填空字符串）：\n"
        "{\n"
        '  "is_interview_related": true 或 false,\n'
        '  "type": "笔试|测评|面试|offer|其他",\n'
        '  "company": "公司名",\n'
        '  "position": "岗位名",\n'
        '  "deadline": "完成截止时间，格式 YYYY-MM-DD HH:MM，仅日期则 YYYY-MM-DD，没有则 null",\n'
        '  "event_time": "面试/笔试实际进行时间，同上，没有则 null",\n'
        '  "link": "测评/笔试/面试链接，没有则空字符串",\n'
        '  "note": "一句话说明，如 截止前需完成在线测评"\n'
        "}"
    )
    try:
        obj = json.loads(call_deepseek(cfg, prompt))
        if isinstance(obj, dict):
            return obj
    except Exception as e:
        print(f"[warn] LLM 抽取失败: {e}")
    return {"is_interview_related": False}


# ---------------------------------------------------------------------------
# 记录管理
# ---------------------------------------------------------------------------
def build_record(mail, info, old=None):
    due = info.get("deadline") or info.get("event_time")
    raw_id = mail.get("messageId") or mail.get("subject") or ""
    rid = hashlib.md5(raw_id.encode("utf-8")).hexdigest()[:12]
    rec = {
        "id": rid,
        "type": info.get("type") or "面试",
        "company": info.get("company") or "",
        "position": info.get("position") or "",
        "due": due,
        "link": info.get("link") or "",
        "note": info.get("note") or "",
        "subject": mail.get("subject", ""),
        "from": mail.get("from", ""),
        "emailDate": mail.get("date"),
        "isDone": False,
        "reminded": {},
        "addedAt": datetime.now().isoformat(timespec="seconds"),
    }
    if old:
        rec["isDone"] = old.get("isDone", False)
        rec["reminded"] = old.get("reminded", {})
    return rec


def pending_records(state, now=None, hide_expired=True):
    now = now or datetime.now()
    out = []
    for r in state["records"].values():
        if r.get("isDone"):
            continue
        if hide_expired:
            due = parse_dt(r.get("due") or "")
            if due is not None and (due - now).total_seconds() < -86400:
                continue
        out.append(r)
    return out


def sort_key(rec):
    d = parse_dt(rec.get("due") or "")
    return (d is None, d or datetime.max)


def compute_reminders(cfg, state, now):
    """返回本次需要提醒的记录 [(rec, stage_days)]"""
    urgent = []
    for rec in pending_records(state, now):
        due = parse_dt(rec.get("due") or "")
        if due is None:
            continue
        remaining_days = (due - now).total_seconds() / 86400
        if remaining_days < -1:
            continue
        reminded = rec.setdefault("reminded", {})
        for d in cfg.get("remindDaysBefore", [3, 1]):
            key = str(d)
            if not reminded.get(key) and remaining_days <= d:
                reminded[key] = True
                urgent.append((rec, d))
                break
    return urgent


# ---------------------------------------------------------------------------
# 报表 & 推送
# ---------------------------------------------------------------------------
def remaining_label(due, now):
    if due is None:
        return "待确认"
    delta = (due - now).total_seconds() / 86400
    if delta < 0:
        return "已过期"
    if delta < 1:
        return "今天/今天内"
    return f"{int(delta)} 天"


def write_report(cfg, state, now):
    recs = sorted(pending_records(state, now), key=sort_key)
    lines = [f"# 📅 面试/测评日程汇总 — {now:%Y-%m-%d}", ""]
    lines.append(f"> 待处理 **{len(recs)}** 项")
    lines.append("")
    if recs:
        lines.append("| 剩余 | 类型 | 公司 | 岗位 | 时间 | 链接 |")
        lines.append("|:--:|:--:|:--|:--|:--|:--|")
        for r in recs:
            due = parse_dt(r.get("due") or "")
            when = due.strftime("%m-%d %H:%M") if due else "未识别"
            link = f"[打开]({r['link']})" if r.get("link") else ""
            lines.append(
                f"| {remaining_label(due, now)} | {r.get('type','')} | {r.get('company','')} "
                f"| {r.get('position','')} | {when} | {link} |"
            )
        lines.append("")
        lines.append("## 明细")
        lines.append("")
        for r in recs:
            title = f"{r.get('company') or '(未知公司)'} · {r.get('position') or ''}".strip(" ·")
            lines.append(f"### {title} — {r.get('type','')}")
            lines.append(f"- 截止/时间: {r.get('due') or '未识别'}")
            if r.get("note"):
                lines.append(f"- 说明: {r['note']}")
            if r.get("link"):
                lines.append(f"- 链接: {r['link']}")
            lines.append(f"- 邮件主题: {r.get('subject','')}")
            lines.append("")
    else:
        lines.append("_暂无待处理事项。_")
        lines.append("")
    lines.append(f"\n*更新于 {now:%Y-%m-%d %H:%M}*")

    report_dir = Path(cfg.get("reportDir"))
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"interview-summary-{now:%Y%m%d}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[report] {path}")
    return path


def build_push_content(urgent, cfg, state, now, include_summary):
    parts = []
    if urgent:
        parts.append("## ⏰ 临近提醒")
        parts.append("")
        for rec, stage in urgent:
            due = parse_dt(rec.get("due") or "")
            when = due.strftime("%m-%d %H:%M") if due else "?"
            line = f"- **{rec.get('company') or '未知公司'}** {rec.get('position') or ''} — {rec.get('type','')}，截止 **{when}**"
            if rec.get("link"):
                line += f"  [链接]({rec['link']})"
            parts.append(line)
        parts.append("")
    if include_summary:
        recs = sorted(pending_records(state, now), key=sort_key)
        parts.append(f"## 📋 全部待处理（{len(recs)}）")
        parts.append("")
        for r in recs:
            due = parse_dt(r.get("due") or "")
            when = due.strftime("%m-%d %H:%M") if due else "未识别"
            parts.append(f"- [{remaining_label(due, now)}] {r.get('company') or '未知公司'} · {r.get('type','')} @ {when}")
        parts.append("")
    parts.append(f"> 更新于 {now:%m-%d %H:%M}")
    return "\n".join(parts)


def pushplus_send(cfg, title, content):
    token = (cfg.get("pushplus", {}) or {}).get("token", "").strip()
    if not token:
        print("[warn] 未配置 pushplus.token，跳过推送。")
        return False
    url = "https://www.pushplus.plus/send"
    payload = {"token": token, "title": title, "content": content, "template": "markdown"}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print(f"[push] {data}")
        return data.get("code") == 200
    except Exception as e:
        print(f"[warn] 推送失败: {e}")
        return False


def wxpusher_send(cfg, title, content):
    wx = cfg.get("wxpusher", {}) or {}
    app_token = (wx.get("appToken") or "").strip()
    uids = [u for u in (wx.get("uids") or []) if u]
    if not app_token:
        print("[warn] 未配置 wxpusher.appToken，跳过推送。")
        return False
    url = "https://wxpusher.zjiecode.com/api/send/message"
    payload = {
        "appToken": app_token,
        "content": content,
        "summary": title,
        "contentType": 3,  # 3 = markdown
        "uids": uids,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print(f"[push] {data}")
        return data.get("code") == 1000
    except Exception as e:
        print(f"[warn] 推送失败: {e}")
        return False


def serverchan_send(cfg, title, content):
    sc = cfg.get("serverchan", {}) or {}
    send_key = (sc.get("sendKey") or "").strip()
    if not send_key:
        print("[warn] 未配置 serverchan.sendKey，跳过推送。")
        return False
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    data = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            r = json.loads(resp.read().decode("utf-8"))
        print(f"[push] {r}")
        return r.get("code") == 0
    except Exception as e:
        print(f"[warn] 推送失败: {e}")
        return False


def send_push(cfg, title, content):
    channel = (cfg.get("pushChannel") or "pushplus").lower()
    if channel == "serverchan":
        return serverchan_send(cfg, title, content)
    if channel == "wxpusher":
        return wxpusher_send(cfg, title, content)
    if channel in ("none", "off", "disabled"):
        print("[push] 推送已禁用。")
        return False
    return pushplus_send(cfg, title, content)


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
def cmd_check(cfg, state):
    now = datetime.now()
    mails = fetch_new_emails(cfg, state)
    print(f"[imap] 新邮件 {len(mails)} 封")

    new_count = 0
    for mail in mails:
        text = f"{mail['subject']} {mail['body']}"
        if not matches_keywords(text, cfg["keywords"]):
            continue
        info = extract_info(cfg, mail)
        if not info.get("is_interview_related"):
            continue
        raw_id = mail.get("messageId") or mail.get("subject") or ""
        rid = hashlib.md5(raw_id.encode("utf-8")).hexdigest()[:12]
        rec = build_record(mail, info, old=state["records"].get(rid))
        state["records"][rid] = rec
        new_count += 1
        print(f"[llm] + {rec['company'] or '未知'} {rec['type']} 截止={rec['due'] or '?'}")

    save_json(STATE_PATH, state)
    print(f"[llm] 相关记录 {new_count} 条")

    write_report(cfg, state, now)

    urgent = compute_reminders(cfg, state, now)
    today = now.strftime("%Y-%m-%d")
    has_pending = bool(pending_records(state, now))
    include_summary = bool(cfg.get("pushDailySummary")) and state.get("lastSummaryDate") != today and has_pending

    if urgent or include_summary:
        content = build_push_content(urgent, cfg, state, now, include_summary)
        title = "📅 面试提醒" if urgent else "📋 面试日程汇总"
        if send_push(cfg, title, content):
            if include_summary:
                state["lastSummaryDate"] = today
    else:
        print("[push] 无临近事项，今日汇总已发送过，跳过。")

    save_json(STATE_PATH, state)


def cmd_report(cfg, state):
    write_report(cfg, state, datetime.now())


def cmd_list(cfg, state):
    now = datetime.now()
    for r in sorted(pending_records(state, now), key=sort_key):
        print(f"{r['id']}  {r.get('due') or '未识别':<20} {r.get('type',''):<4} "
              f"{r.get('company','')} {r.get('position','')}")
    print(f"共 {len(pending_records(state, now))} 项待处理")


def cmd_done(cfg, state, rid):
    hits = [k for k in state["records"] if k.startswith(rid)]
    if not hits:
        print(f"[warn] 未找到匹配 '{rid}' 的记录")
        return
    for k in hits:
        state["records"][k]["isDone"] = True
    save_json(STATE_PATH, state)
    print(f"[ok] 已标记 {len(hits)} 条为完成")


def main():
    ap = argparse.ArgumentParser(description="面试/测评邮件追踪器")
    ap.add_argument("command", nargs="?", default="check",
                    choices=["check", "report", "test-push", "list", "done"])
    ap.add_argument("id", nargs="?", help="done 命令使用的记录 id 前缀")
    args = ap.parse_args()

    cfg = load_config()

    if args.command == "test-push":
        send_push(cfg, "✅ 推送测试", "收到这条消息说明推送配置成功。")
        return

    state = load_json(STATE_PATH, {"last_uid": 0, "records": {}, "lastSummaryDate": ""})
    state.setdefault("records", {})
    state.setdefault("lastSummaryDate", "")

    if args.command == "check":
        cmd_check(cfg, state)
    elif args.command == "report":
        cmd_report(cfg, state)
    elif args.command == "list":
        cmd_list(cfg, state)
    elif args.command == "done":
        if not args.id:
            print("[error] 用法: python tracker.py done <id前缀>")
            sys.exit(1)
        cmd_done(cfg, state, args.id)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[fatal] {e}")
        sys.exit(1)
