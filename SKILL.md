---
name: interview-mail-tracker
description: "监控邮箱中的面试/笔试/在线测评邮件，用大模型抽取截止时间，到期前经 Server酱/PushPlus/WxPusher 推送到微信，并生成 Markdown 日程汇总报表。Use when user mentions '面试提醒', '笔试提醒', '测评截止', '求职邮件追踪', 'interview email tracker', or asks to set up interview/assessment deadline reminders from their mailbox. Trigger command: /interview-tracker."
---

# 面试 / 测评邮件追踪器

从邮箱（IMAP）抓取求职相关邮件，抽取截止时间，微信提醒并生成日程报表。

## 命令

### `/interview-tracker`

引导用户完成配置（邮箱授权码 / 大模型 Key / 推送凭证），并运行或安装定时任务。

---

## 脚本目录

**重要**：脚本位于此 skill 的根目录与 `scripts/` 子目录。

**Agent 执行说明**：
1. 确定此 SKILL.md 所在目录为 `SKILL_DIR`
2. 主脚本路径 = `${SKILL_DIR}/tracker.py`
3. 示例配置 = `${SKILL_DIR}/config.example.json`

| 文件 | 用途 |
|------|------|
| `tracker.py` | 主脚本（纯标准库，无第三方依赖） |
| `config.example.json` | 配置模板 |
| `config.json` | 用户本地配置（敏感，勿提交/外传） |
| `state.json` | 运行状态（含个人记录） |

---

## 交互流程

### Step 0：检查配置

```powershell
Test-Path "${SKILL_DIR}\config.json"
```

- 若存在且已填好凭证 → 询问是否直接运行
- 否则进入 Step 1 引导配置

### Step 1：收集凭证

向用户询问（缺失项才问）：

| 凭证 | 获取方式 |
|------|----------|
| QQ 邮箱 IMAP 授权码 | mail.qq.com → 设置 → 账户 → 开启 IMAP/SMTP → 生成 16 位授权码 |
| 大模型 API Key | https://platform.deepseek.com/api_keys |
| Server酱 SendKey | https://sct.ftqq.com 微信扫码 → 复制 SendKey |

### Step 2：写入配置

复制 `config.example.json` 为 `config.json`，填入用户凭证（**不要**把凭证输出到对话以外的日志/文件）。

### Step 3：测试与运行

```bash
python "${SKILL_DIR}/tracker.py" test-push   # 测试推送
python "${SKILL_DIR}/tracker.py" check       # 跑一次完整流程
```

### Step 4：安装定时任务（Windows）

```powershell
schtasks /Create /TN "InterviewTracker" /TR "${SKILL_DIR}\run.bat" /SC DAILY /ST 09:00 /F
```

> 记得先把 `run.bat` 里的 Python 路径改成用户本机路径。

---

## 常用命令

```bash
python tracker.py check       # 抓邮件 + 抽取 + 报表 + 提醒
python tracker.py report      # 只重生成报表
python tracker.py list        # 列出待处理
python tracker.py done <id>   # 标记完成
python tracker.py test-push   # 测试推送
```

---

## 环境要求

- Python 3.8+
- 一个支持 IMAP 的邮箱
- 一个 OpenAI 兼容大模型 Key（默认 DeepSeek）
- 一个推送渠道凭证（Server酱 / PushPlus / WxPusher）

---

## 故障排除

- `未配置 xxx，跳过推送` → 对应渠道凭证未填，检查 `config.json`
- `LLM 抽取失败` → 检查大模型 Key / 余额 / 网络
- `用户未实名` → PushPlus 需实名，改用 Server酱（`pushChannel: "serverchan"`）
- 收不到微信 → 确认已关注对应公众号，或换 `pushChannel`
