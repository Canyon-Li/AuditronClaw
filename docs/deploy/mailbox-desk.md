# 邮箱事务台部署清单

事务台是本项目的示例场景(也是作者自用的第一个真实任务):定时只读近期邮箱 → 分类总结 → 待办落盘 → 摘要推送飞书。
本文把展品从代码变成日常运行:凭据三项就位、实弹验证一轮、建 daily 循环任务、回滚方法。

设计原则回顾(细节见 spec 与 `CONTEXT.md` 词条):

- **网络能力是命名工具**,不是匿名 socket:只有 `read_recent_emails`(只读)与
  `send_feishu_summary`(推送)两个出口,各自绑定唯一目标域,域名白名单守卫代码强制。
- **凭据只存在宿主机 .env**(信任面):工具参数、返回值、LLM 上下文、审计日志
  四个执行面里永远不出现授权码与 webhook URL。
- **零新调度概念**:定时触发 = 用现有 `schedule_task` 建一条 `repeat="daily"`
  的循环任务,心跳引擎到期把管线消息塞进会话队列,与手动对话共用同一审计流。

## 一、凭据三项(宿主机 .env)

复制 `.env.example` 为 `.env`(已 gitignore),补三项:

| 环境变量 | 哪里拿 | 说明 |
| --- | --- | --- |
| `MAIL_ACCOUNT` | 你的 QQ 邮箱地址 | 如 `someone@qq.com` |
| `MAIL_IMAP_PASSWORD` | QQ 邮箱网页版 → 设置 → 账户 → 开启 IMAP/SMTP 服务 → 生成授权码 | **不是登录密码**,是 16 位授权码 |
| `FEISHU_WEBHOOK_URL` | 飞书群 → 设置 → 群机器人 → 添加自定义机器人(免签) | 完整 webhook URL |

未配置时的行为:两个工具返回结构化错误(不碰网络),不影响其他功能。

**核验凭据不残留**(部署后任意时刻可跑):

```powershell
# 审计日志与工作区全文不含授权码 / webhook 串(换成你的真实凭据片段)
Select-String -Path logs/*.jsonl,workspace/tasks.json -Pattern "你的授权码片段","hook/你的token片段"
# 仓库不追踪 .env
git status --short   # .env 不应出现在任何改动里
git log --all --oneline -- .env   # 应无输出:.env 从未入库
```

## 二、实弹一轮(部署前验证)

在会话里直接说(`auditronclaw run` 后任意输入):

> 跑一轮邮箱事务台,共两步:
> 1. 调用 read_recent_emails(hours=24, max_emails=10) 读取近期 24 小时邮件,只调用一次。
> 2. 把分类结果作为参数调用 submit_mailbox_desk_report 一次性提交——分类规则
>    在该工具的说明里。日报排版、待办落盘、飞书推送都由该工具完成,不要再调
>    send_feishu_summary 或 schedule_task。

管线只有两次工具调用:agent 负责**分类判断**(填结构化字段),「分类账」渲染、
待办落盘、推送全部由 `submit_mailbox_desk_report` 代码完成——格式、顺序、副作用
不依赖模型自觉(这是弱模型实弹教训换来的设计,见已知边界)。

验收(操作员人工确认):

- [ ] 飞书群真的收到日报(不是 agent 嘴上说推了)
- [ ] `workspace/tasks.json` 出现从邮件提炼的待办(双锚口径:submit 工具调用 +
      落盘终态,可在 monitor 面回放审计事件)
- [ ] 邮件分类符合预期(待办没因为"是通知"被漏掉)

## 三、建 daily 循环任务

实弹验收通过后,在会话里说:

> 以后每天 08:00 跑一轮邮箱事务台,repeat 设为 daily。任务描述就用上面那段
> 两步管线指令原文,每天到点按它执行。

agent 会调用 `schedule_task(target_time="<下一个08:00>", description=<两步指令全文>, repeat="daily")`。
之后每天到点,心跳引擎把【系统内部心跳触发】+ 指令塞进会话队列,agent 照常执行——
与手动对话共用同一会话机制与审计流,自主动作不绕过任何一层防线。

**触发观测**:`auditronclaw run` 挂着即可;到期那轮会自己开始。
想先演练不等到明天,可临时建一条两分钟后到期的同款任务,观察它触发后续期到明天
(自动演练已钉在 `tests/test_heartbeat.py::TestPacemakerLoopDailyDeskTask`)。

## 四、域名白名单(可选扩展)

两个网络工具的目标域默认名单是 `imap.qq.com` 与 `open.feishu.cn`,开箱即用,无需配置。
守卫校验的是**工具代码绑定的常量域**(LLM 的工具参数里没有 URL 字段),名单外域名
被拒绝并落审计事件。

`AUDITRONCLAW_ALLOWED_DOMAINS` 环境变量可向名单追加域(逗号分隔),扩展生效记
审计事件——与命令白名单(`AUDITRONCLAW_ALLOWED_COMMANDS`)同构:扩展是一次
显式授权,留痕可查:

```
AUDITRONCLAW_ALLOWED_DOMAINS=api.github.com
```

当前两个工具的目标域都在默认名单里,此变量平时用不上;它是为将来新增命名网络
工具或部署者显式授权预留的口子。**换邮箱服务商或推送通道不是改 .env 一行的事**:
新域要先写进工具绑定(代码常量)并进入默认名单,环境变量只放行、不指路。

## 五、推送失败的行为(预期,不用慌)

断网 / webhook URL 失效时:

- **待办仍落盘**——`submit_mailbox_desk_report` 内部顺序写死"先落待办、后推送",
  代码强制,推送失败不吞当天事务;
- **错误结构化可查**——回执如实报告"待办 N 项已落任务列表,但推送未成功",
  审计日志留 `飞书推送失败` 事件(monitor 面可检索),错误文案不含 webhook URL;
- **下轮不受污染**——失败不留状态,网络恢复后下一轮正常推送。

自动演练钉在 `tests/test_mailbox_desk_drill.py` 与 `tests/test_desk_submit_tool.py`。

## 六、回滚(删任务即停)

在会话里说:

> 删掉邮箱事务台的循环任务。

agent 调用 `delete_scheduled_task(任务id)`(id 可先问 `list_scheduled_tasks`)。
删除后心跳不再触发,已落盘的历史待办保留在 tasks.json 由你处置。
**彻底停用网络能力**:同时从 .env 删掉三项凭据并清掉 `workspace/tasks.json`
里的事务台任务即可——没有任何隐藏状态。

## 已知边界(诚实披露)

- **控制面已代码化,分类判断仍是模型的**:事务台从"3 次工具调用 + 自然语言
  约束格式与顺序"重构为"2 次调用 + 结构化提交"(2026-08-23 实弹拍板)——
  此前 glm-4-flash 在旧管线下读到邮件后不调落盘/推送工具,直接在对话里输出
  日报并谎称"已推送已存入",分步强制指令也救不回来;现在格式渲染、待办落盘、
  推送顺序全部由 `submit_mailbox_desk_report` 代码强制,模型只剩分类判断一个
  自由度。残余风险:分错类(如把通知当需回复)——日报仍以飞书群实际收到为准,
  分类质量靠观察期人工抽查。
- 双基准数字(含 email 注入面)见 `benchmarks/RESULTS.md` 与 README 安全基准节;
  golden 事务台用例对弱模型的 miss_tool 形态在基准结果文件里如实入册。
- 被骗的合法推送(骗 agent 把不该推的内容用真凭据推出去)是合法调用,本层防线
  不拦——已知边界,归审批门(策略层)立项处理。
