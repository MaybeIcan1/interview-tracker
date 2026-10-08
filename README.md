# 🎯 面试 / 测评邮件追踪器

自动监控你的邮箱，识别 **面试邀请、笔试、在线测评** 等求职邮件，抽取关键信息（公司 / 岗位 / 类型 / 截止时间 / 链接），
在 **微信** 上按 `提前 3 天`、`提前 1 天` 提醒你，并生成一份可读的日程汇总报表。

- 🪶 纯 Python 标准库，**零第三方依赖**
- 🤖 用大模型（DeepSeek，OpenAI 兼容接口）做结构化信息抽取
- 📲 推送到微信：支持 **Server酱** / PushPlus / WxPusher，可一键切换
- ⏰ 交给系统定时任务（Windows 计划任务 / Linux cron）后台跑
- 📅 报表为 Markdown，可直接在 Obsidian / VS Code 里看

---

## 🔧 工作原理

```
系统定时任务（如每天 9:00）
        │
        ▼
IMAP 拉取邮箱新邮件（UID 增量，只处理新邮件）
        │
        ▼
关键词预筛（省 token，命中才送 LLM）
        │
        ▼
DeepSeek 抽取 → { 类型, 公司, 岗位, 截止时间, 链接 }
        │
        ▼
写入 state.json（去重、记录提醒状态）
        │
        ├──▶ 生成 Markdown 日程报表
        │
        └──▶ 到期前 3 天 / 1 天 → 微信推送
```

---

## 🚀 快速开始

### 1. 环境要求

- Python **3.8+**（无需安装任何第三方库）
- 一个支持 IMAP 的邮箱（本文以 **QQ 邮箱** 为例）
- 一个 DeepSeek API Key（或任意 OpenAI 兼容接口）
- 一个推送渠道的凭证（推荐 Server酱）

### 2. 获取代码

```bash
git clone <你的仓库地址>
cd interview-tracker
```

### 3. 创建配置

复制示例配置并填写：

```bash
copy config.example.json config.json    # Windows
cp config.example.json config.json      # macOS / Linux
```

### 4. 填写三个凭证

见下方 [申请凭证](#-申请凭证)，分别填入 `config.json` 的 `imap`、`deepseek`、`serverchan`。

### 5. 测试

```bash
python tracker.py test-push   # 测试微信推送
python tracker.py check       # 手动跑一次：抓邮件 + 抽取 + 报表 + 提醒
```

### 6. 配置定时任务

见下方 [定时任务](#-定时任务)。

---

## 🔑 申请凭证

### ① QQ 邮箱 IMAP 授权码

1. 登录 [mail.qq.com](https://mail.qq.com) → **设置 → 账户**
2. 找到「**POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务**」
3. 开启「**IMAP/SMTP 服务**」（需短信验证）
4. 复制生成的 **16 位授权码**（这不是登录密码）

填入 `config.json`：

```json
"imap": {
  "host": "imap.qq.com",
  "port": 993,
  "user": "你的QQ号@qq.com",
  "authCode": "16位授权码"
}
```

> 其他邮箱改 `host` 即可：163 → `imap.163.com`，Gmail → `imap.gmail.com`（Gmail 需用「应用专用密码」）。

### ② DeepSeek API Key

1. 访问 [platform.deepseek.com](https://platform.deepseek.com/api_keys)
2. 创建 API Key（形如 `sk-...`）
3. 填入 `config.json` 的 `deepseek.apiKey`

> 兼容任意 OpenAI 格式接口，改 `apiBase` / `model` 即可（如通义千问、Kimi、本地 Ollama 等）。

### ③ Server酱 SendKey（推荐推送渠道）

1. 打开 [sct.ftqq.com](https://sct.ftqq.com) → **微信扫码登录**（会引导关注「Server酱」公众号）
2. 复制 **SendKey**（形如 `SCT...`）
3. 填入 `config.json` 的 `serverchan.sendKey`

> 免费版每天约 5 条消息，日常提醒足够。

---

## ⚙️ 配置说明（`config.json`）

| 字段 | 说明 | 默认 |
|------|------|------|
| `imap.host` / `imap.port` | 邮箱 IMAP 服务器 | `imap.qq.com` / `993` |
| `imap.user` | 邮箱地址 | 必填 |
| `imap.authCode` | IMAP 授权码 | 必填 |
| `deepseek.apiKey` | 大模型 Key | 必填 |
| `deepseek.apiBase` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `deepseek.model` | 模型名 | `deepseek-chat` |
| `pushChannel` | 推送渠道：`serverchan` / `pushplus` / `wxpusher` / `none` | `serverchan` |
| `serverchan.sendKey` | Server酱 SendKey | — |
| `pushplus.token` | PushPlus token（PushPlus 需实名） | — |
| `wxpusher.appToken` / `wxpusher.uids` | WxPusher 应用与用户 | — |
| `keywords` | 命中这些词才送 LLM 抽取（省 token） | 见示例 |
| `remindDaysBefore` | 提前几天提醒，数组 | `[3, 1]` |
| `lookbackDays` | 首次运行回看天数 | `2` |
| `reportDir` | 报表输出目录 | `./reports` |
| `pushDailySummary` | 每天是否推一条汇总 | `true` |

---

## 📖 命令

```bash
python tracker.py check       # 抓邮件 + 抽取 + 报表 + 提醒（定时任务用这个）
python tracker.py report      # 只用现有数据重生成报表，不联网
python tracker.py list        # 列出所有待处理记录
python tracker.py done <id>   # 标记某条记录为已完成（id 用 list 里的前几位）
python tracker.py test-push   # 测试推送渠道
```

---

## ⏰ 定时任务

### Windows（计划任务）

把 `run.bat` 里的 Python 路径改成你自己的，然后：

```powershell
schtasks /Create /TN "InterviewTracker" /TR "D:\path\to\interview-tracker\run.bat" /SC DAILY /ST 09:00 /F
```

- 查看：`schtasks /Query /TN "InterviewTracker"`
- 删除：`schtasks /Delete /TN "InterviewTracker" /F`
- 立即跑一次：`schtasks /Run /TN "InterviewTracker"`

### Linux / macOS（cron）

```bash
crontab -e
# 每天 9:00 运行
0 9 * * * cd /path/to/interview-tracker && python3 tracker.py check >> logs/run.log 2>&1
```

---

## 🔀 切换推送渠道

改 `config.json` 的 `pushChannel` 即可，代码已内置三种：

| 渠道 | 值 | 特点 |
|------|-----|------|
| **Server酱** | `serverchan` | 微信服务号，免费约 5 条/天，推荐 |
| PushPlus | `pushplus` | 微信推送，**需实名认证** |
| WxPusher | `wxpusher` | 多端推送（当前主推客户端，微信通道受限） |
| 关闭 | `none` | 只生成本地报表，不推送 |

> 💡 2026 年起微信官方收紧了公众号推送，各平台的微信公众号通道都有变化；
> 若你所在渠道失效，换一个 `pushChannel` 即可，无需改代码。

---

## ❓ 常见问题

**Q：首次运行会不会把历史邮件全抓进来？**
不会。首次运行只处理 `lookbackDays` 天内的邮件（按服务器接收时间 INTERNALDATE 判断），之后按 UID 增量只抓新邮件。

**Q：会不会把整个邮箱都读一遍，很费 token？**
只有**命中关键词**、且被判定与求职相关的新邮件才会送 LLM，日常消耗极小（一个月通常几毛钱）。

**Q：QQ 邮箱搜索 `SINCE` 不生效？**
QQ IMAP 会忽略日期搜索条件，本工具因此改用更可靠的 **UID 增量**方式。

**Q：报表在哪看？**
`reportDir` 目录下按天生成 `interview-summary-YYYYMMDD.md`，可直接用 Obsidian / VS Code 打开（支持表格渲染）。

**Q：怎么标记某个面试已完成？**
`python tracker.py list` 找到 id，再 `python tracker.py done <id前缀>`。

---

## 🔒 隐私与安全

- `config.json`（邮箱授权码、API Key、推送 token）与 `state.json`（个人面试记录）**已在 `.gitignore` 中排除**，不会被提交。
- 所有数据仅保存在本机，除调用你配置的邮箱服务器 / 大模型接口 / 推送服务外，不向任何第三方发送。
- 请勿把 `config.json` 提交到公开仓库。

---

## 📄 License

MIT
