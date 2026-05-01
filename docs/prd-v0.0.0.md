# 个人 AI 投资分析代理 — 构建蓝图

本方案用于构建一个 **7×24 小时** 自动化投资分析代理。代理运行在本地 **Mac mini** 上，由 **Claude Code** 驱动编排与推理，以 **OpenBB SDK**（及 FMP/FRED 等数据源）为核心数据能力，最终将分析结果推送给你，辅助你做出 **手动交易决策**。

---

## 核心理念

- **自主可控**：核心逻辑、持仓数据与运行环境均部署在你自己的本地硬件上，保障隐私与控制权。
- **低成本运行**：充分利用本地算力与高质量免费 API 数据源，模型侧成本主要集中在 Claude / Claude Code 相关调用。
- **原生与简洁**：不自写「主控脚本 + 工具注册」样板代码，由 **Claude Code** 负责会话、工具调用循环与任务拆解；需要时用终端执行小段 Python 或薄脚本调用 OpenBB 即可。
- **人机结合**：AI 负责自动化采集与分析并给出专业建议；你保留最终决策与操作权。

---

## 技术栈

| 类别 | 工具/技术 | 作用 |
|------|-----------|------|
| 硬件主机 | 你的 Mac mini | 提供 7×24 小时不间断的本地运行环境 |
| 编排与推理 | **Claude Code** | 项目内驱动分析流程：读持仓、拉数据、多轮推理、汇总报告；替代自写的 `main.py` 主循环 |
| 核心大脑 (Brain) | Claude 模型（经 Claude Code） | 理解数据、深度分析、形成观点并生成投资建议 |
| 核心数据工具 (Data Engine) | OpenBB SDK | 在需要时由 Claude Code 通过终端/脚本调用，获取金融市场与宏观数据 |
| 数据源 API (Data Sources) | Financial Modeling Prep (FMP) & FRED | **关键**：申请免费 API Key，供 OpenBB 使用 |
| 持仓管理 (Portfolio) | 本地 CSV（`portfolio.csv`） | 简单、透明的持仓记录 |
| 任务调度 (Scheduler) | macOS `cron` 或 `launchd` | 定时触发一次「分析运行」（见下文：调用 Claude Code 的入口，而非 `python main.py`） |
| 开发环境 | Python 3 + 虚拟环境（`venv`） | 安装 `openbb`、`pandas` 等，供 Claude Code 在终端中按需执行 |

---

## 工作流程

1. **触发 (Trigger)**  
   定时任务在预设时间启动一次分析。入口是 **Claude Code**（例如在项目根目录执行官方 CLI，或由 `cron` 调用你写的 **薄包装脚本**——内部仍然是唤起 Claude Code 并传入固定指令/上下文），**不再**单独维护 `main.py` 作为常驻主进程。

2. **加载状态 (Load State)**  
   Claude Code 按项目约定读取 `portfolio.csv`（或你在 `CLAUDE.md` 里写明的路径与格式），了解当前持仓标的、数量与成本。

3. **采集数据 (Data Collection)**  
   - 按持仓遍历标的。  
   - 在终端中运行 Python（已激活 `venv`、已配置 FMP/FRED Key），通过 **OpenBB SDK** 拉取价格、技术指标、新闻等——可直接执行片段命令，或调用你自愿保留的 **极薄** 辅助脚本（仅封装重复命令，**不必**再拆成完整的 `tools.py` 工具层）。

4. **思考与分析 (Think & Analyze)**  
   由 **Claude Code** 整合持仓与数据、多轮推理；若需补充宏观或另类数据，继续在会话内调用终端/Python 即可，无需自行实现 Anthropic SDK 的 `tools` 注册与循环。

5. **生成报告 (Generate Report)**  
   在同一会话中输出结构化报告：市场解读、风险评估、操作建议（持有、减仓、补仓及理由）。

6. **通知 (Notification)**  
   由 Claude Code 执行你约定的后续步骤（例如运行发送邮件/Telegram 的小脚本，或写入 `agent.log`）。若无自动化寄送，也可仅在会话中交付，由你手动复制。

7. **结束 (Termination)**  
   本次运行结束，等待下一次定时触发。

---

## 项目文件结构（无 `main.py` / `tools.py`）

```text
my-trading-agent/
├── CLAUDE.md               # 给 Claude Code 的项目说明：流程、路径、API Key 环境变量约定等
├── portfolio.csv           # 持仓数据
├── requirements.txt        # openbb、pandas 等（数据侧依赖）
├── venv/                   # Python 虚拟环境
├── scripts/                # （可选）仅薄封装重复命令，例如一日一用的 fetch 片段
└── agent.log               # （可选）运行日志
```

定时任务示例：调用 **包装脚本**（如 `run-analysis.sh`）在项目目录下启动 Claude Code 并附带固定提示；**不要**再指向 `python .../main.py`。

---

## 实施步骤

### 1. 环境设置

- 在 Mac mini 上创建项目目录 `my-trading-agent`。  
- 创建并激活虚拟环境，安装数据依赖：

  ```bash
  python3 -m venv venv
  source venv/bin/activate
  pip install openbb pandas
  ```

  可将依赖写入 `requirements.txt` 后用 `pip install -r requirements.txt` 安装。

- 安装并登录 **Claude Code**，确保能在该项目目录下正常启动会话。

### 2. 获取免费 API Keys（关键）

- 在 [Financial Modeling Prep (FMP)](https://financialmodelingprep.com/) 注册并获取免费套餐的 API Key。  
- 在 [FRED (St. Louis Fed)](https://fred.stlouisfed.org/) 注册并申请 API Key。  
- 建议通过 **环境变量** 或 `.env`（勿提交仓库）注入，在 `CLAUDE.md` 中写明变量名与 OpenBB 配置方式（例如 `obb.keys.fmp`、`obb.keys.fred`）。

### 3. 用 Claude Code 落地（替代手写 `main.py` / `tools.py`）

- 编写 **`CLAUDE.md`**：说明每日/每小时分析流程、读取 `portfolio.csv` 的规则、如何用 `venv` 里的 Python 调用 OpenBB、报告格式与通知方式。  
- 在会话中让 Claude Code 直接执行终端命令完成取数与分析；仅当某段命令重复多次时，再抽到 `scripts/` 下几行薄封装——**不强制**单独的 `tools.py` 模块。  
- API Key 与 FMP/FRED 配置：放在环境或一次性初始化脚本中，由 `CLAUDE.md` 引用，避免把密钥写进提示词历史。

### 4. 配置定时任务

- 执行 `crontab -e`，添加在期望时间进入项目目录并 **启动一次 Claude Code 分析** 的命令（具体命令以你本机 Claude Code 的官方 CLI 为准），例如概念上：

  ```cron
  # 每个工作日上午 9 点与下午 1 点触发一次分析（示例：请替换为实际的 claude / 包装脚本路径）
  0 9,13 * * 1-5 cd /Users/yourname/my-trading-agent && ./run-analysis.sh
  ```

将路径与 `run-analysis.sh` 的内容按你的安装方式调整；核心是 **定时唤起 Claude Code**，而不是 `python main.py`。
