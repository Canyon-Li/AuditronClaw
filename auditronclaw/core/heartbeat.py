import os
import json
import asyncio
import calendar
from datetime import datetime, timedelta
from .tools.builtins import (
    TASK_TIME_FORMAT,
    _validated_tasks,
    _write_tasks,
    tasks_lock,
)
from .approval.gate import TurnOrigin
from .bus import TurnRequest
from .logger import get_audit_logger

# 读盘失败去抖（F3）：上次已记回执的错误签名。坏 JSON 在修复前每个
# 周期都撞同一处，回执只记首次；读盘恢复正常即重置——下一次失败是
# 新一案，照记。
_last_read_error_key: str | None = None


def _next_occurrence(target_dt: datetime, repeat: str) -> datetime:
    """循环任务的下一次触发时刻。

    repeat 经 ScheduledTask 的 Literal 校验，取值穷尽四值；
    monthly 按次月同日推进，月末按次月最后一天钳制（1月31日 → 2月28/29日），
    12月跨年。
    """
    if repeat == "hourly":
        return target_dt + timedelta(hours=1)
    if repeat == "daily":
        return target_dt + timedelta(days=1)
    if repeat == "weekly":
        return target_dt + timedelta(days=7)
    # monthly
    month = target_dt.month + 1
    year = target_dt.year
    if month > 12:
        month = 1
        year += 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(target_dt.day, last_day)
    return target_dt.replace(year=year, month=month, day=day)


async def pacemaker_loop(task_queue: asyncio.Queue, tasks_file: str, check_interval: int = 10):
    """
    后台心脏起搏器协程（带并发锁和循环任务续期功能）。

    队列落点(tasks_file)为装配期入参（05 票）：与任务工具同一文件由
    入口装配时传入，本模块不持有路径常量。
    """
    while True:
        await asyncio.sleep(check_interval)

        if not os.path.exists(tasks_file):
            continue

        now = datetime.now()
        pending_tasks = []
        triggered_tasks = []

        global _last_read_error_key

        #线程锁，防止多线程/多协程同时读写任务文件导致的竞争条件和数据损坏
        with tasks_lock:
            try:
                with open(tasks_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        _last_read_error_key = None
                        continue
                    raw_tasks = json.loads(content)
                    _last_read_error_key = None
            except Exception as e:
                # 读失败落回执（F3，替换原裸 except 的静默空转）：历史损伤
                # JSON 曾让心跳永久无回执空转。去抖——同一错误只记一次。
                error_key = f"{type(e).__name__}: {e}"
                if error_key != _last_read_error_key:
                    _last_read_error_key = error_key
                    get_audit_logger().log_event(
                        thread_id="system",
                        event="system_action",
                        content=(
                            f"心跳读任务队列失败（{error_key}）：{tasks_file}。"
                            "本轮跳过——不触发、不续期；同一错误持续不再重复"
                            "记录，修复后恢复正常记录。"
                        ),
                    )
                continue

            if not raw_tasks:
                continue

            # 读盘边界统一过模型：校验失败的条目已在 _validated_tasks 内
            # 记审计回执并跳过（替换原裸 except 的静默吞掉）——不触发、
            # 不续期，写回时自然移出队列
            valid_tasks = _validated_tasks(raw_tasks)
            dropped_invalid = len(valid_tasks) < len(raw_tasks)

            for t in valid_tasks:
                # target_time 格式由 ScheduledTask 保证可解析，此处不会抛错
                target_dt = datetime.strptime(t.target_time, TASK_TIME_FORMAT)
                if now >= target_dt:

                    triggered_tasks.append(t) #记录为“需要触发”

                    #如果是循环任务就把次数减1，次数耗尽就不再触发
                    if t.repeat:
                        if t.repeat_count is not None:
                            if t.repeat_count <= 1:
                                continue
                            t.repeat_count = t.repeat_count - 1

                        t.target_time = _next_occurrence(
                            target_dt, t.repeat).strftime(TASK_TIME_FORMAT)
                        pending_tasks.append(t)
                else:

                    pending_tasks.append(t) #还未到触发时间的任务继续保留在任务队列里

            #触发过任务或校验移除过坏条目，才把存留任务写回文件——
            #经 model_dump 走 _write_tasks 原子替换
            if triggered_tasks or dropped_invalid:
                try:
                    _write_tasks([t.model_dump() for t in pending_tasks], tasks_file)
                except Exception as e:
                    # 写回失败落回执（F3，替换原静默 pass）：当轮触发消息
                    # 照发，但续期/移出未落盘，队列文件保持旧内容——
                    # 丢失必须可见，不吞。
                    get_audit_logger().log_event(
                        thread_id="system",
                        event="system_action",
                        content=(
                            f"心跳写回任务队列失败"
                            f"（{type(e).__name__}: {e}）：{tasks_file}。"
                            "本轮触发消息照发，但续期与移出未落盘，"
                            "队列文件保持旧内容。"
                        ),
                    )

        for t in triggered_tasks:
            system_msg = (
                f"【系统内部心跳触发】\n"
                f"你设定的定时任务已到期，请立即主动提醒用户或执行动作。\n"
                f"任务内容：{t.description}"
            )
            # 来源类型化(frozen 信封):心跳回合在构造上即无人值守,
            # 前缀文本只是给模型看的提示,不再承担来源标记职责
            await task_queue.put(TurnRequest(text=system_msg,
                                             origin=TurnOrigin.HEARTBEAT))
