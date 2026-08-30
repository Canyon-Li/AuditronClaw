"""审计日志:内存队列 + 守护线程写 JSONL。

装配期工厂(05 票):入口 init_audit_logger(log_dir) 构造一次、显式注入
落点;消费方 get_audit_logger() 取用,未初始化即拒绝——无审计不运行。
单例性由模块持有(_audit_logger),类本身是普通构造器:log_dir 必填,
构造即启动自检,审计位置不随启动目录漂移、不在 import 期冻结。
"""
import os
import json
import threading
import queue
import atexit
from datetime import datetime, timezone

FALLBACK_FILE = "audit_fallback.jsonl"
_PROBE_FILE = ".startup_probe"

# 进程级实例与初始化锁:init 幂等、get 快路径无锁读
_audit_logger: "JSONLEventLogger | None" = None
_init_lock = threading.Lock()


class JSONLEventLogger:
    """审计写盘器:无界内存队列缓冲,后台线程写,主写失败落同目录兜底文件。"""

    def __init__(self, log_dir: str):
        # 落点必填:装配方(入口/基准)注入,logger 不自带默认位置
        self.log_dir = log_dir
        self._self_check_log_dir()

        # 无界内存队列，用于缓冲日志事件
        self.log_queue = queue.Queue()

        self.worker_thread = threading.Thread(target=self._write_loop, daemon=True)
        self.worker_thread.start()

        # 确保程序被关闭时，队列里的剩下日志能写完
        atexit.register(self.shutdown)

    def _self_check_log_dir(self):
        """启动自检：探针事件写读一圈。审计不可写即拒绝启动——凭证归零比宕机更不可接受。"""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            # 探针文件按进程区分：并发启动的两个进程各写各的探针，
            # 不会删掉对方的读回目标；崩溃残留的旧探针无人认领，也不影响后续自检
            probe_path = os.path.join(self.log_dir, f"{_PROBE_FILE}.{os.getpid()}")
            payload = json.dumps({"event": "audit_startup_probe"}, ensure_ascii=False)
            with open(probe_path, "w", encoding="utf-8") as f:
                f.write(payload + "\n")
            with open(probe_path, encoding="utf-8") as f:
                if "audit_startup_probe" not in f.read():
                    raise OSError("探针读回内容不符")
            try:
                os.remove(probe_path)
            except OSError:
                pass  # 删不掉不影响审计：下次自检写的是自己的探针文件
        except OSError as e:
            raise RuntimeError(
                f"审计启动自检失败：LOG_DIR {self.log_dir} 不可写或不可读（{e}）——无审计不运行，拒绝启动"
            ) from e

    # 后台线程的死循环：一直盯着队列，有日志就写，没日志就阻塞休眠
    def _write_loop(self):
        while True:
            log_item = self.log_queue.get()

            if log_item is None:
                self.log_queue.task_done()
                break

            try:
                self._write_item(log_item)
            except Exception as e:
                # 兜底也失败：磁盘连日志目录都写不进属灾难场景，打印是诚实极限。
                # 循环必须活着——写线程崩溃了，后续事件连被兜底的资格都没有
                print(f"[Logger Error] 主写与兜底均失败,事件丢弃: {e}")
            finally:
                self.log_queue.task_done()

    def _write_item(self, log_item: dict):
        thread_id = log_item.get("thread_id", "system")
        safe_id = "".join(c for c in thread_id if c.isalnum() or c in "-_") or "default"
        file_path = os.path.join(self.log_dir, f"{safe_id}.jsonl")

        try:
            self._append_jsonl(file_path, log_item)
        except Exception as e:
            # 主写失败（磁盘故障，或事件含不可序列化值）：事件落同目录兜底文件，
            # 痕迹必可发现，不静默丢弃
            fallback_item = {**log_item, "fallback_reason": f"{type(e).__name__}: {e}"}
            self._append_jsonl(
                os.path.join(self.log_dir, FALLBACK_FILE), fallback_item,
                stringify_values=True,
            )

    @staticmethod
    def _append_jsonl(file_path: str, item: dict, stringify_values: bool = False):
        # 主路径严格序列化（失败即触发兜底）；兜底路径宽容序列化——
        # 不可序列化的值降级为字符串，保证兜底本身不因同一原因再失败
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(
                item, ensure_ascii=False,
                default=str if stringify_values else None,
            ) + "\n")

    # 前台调用的埋点方法
    def log_event(self, thread_id: str, event: str, **kwargs):
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        log_item = {
            "ts": now_utc,
            "thread_id": thread_id,
            "event": event,
            **kwargs
        }

        self.log_queue.put(log_item)

    def shutdown(self):
        self.log_queue.put(None)
        self.log_queue.join()


def init_audit_logger(log_dir: str) -> JSONLEventLogger:
    """装配期初始化（入口构造一次）：显式注入落点，构造即自检。

    幂等：同落点重复初始化返回既有实例；换落点拒绝——审计位置装配后
    固化，静默换址等于既有凭证失去可发现性。构造失败不留半初始化实例
    （赋值发生在构造成功之后），下次初始化重新走自检。
    """
    global _audit_logger
    with _init_lock:
        if _audit_logger is not None:
            if _audit_logger.log_dir != log_dir:
                raise RuntimeError(
                    f"审计落点已固化于 {_audit_logger.log_dir!r}，拒绝换址到 {log_dir!r}"
                    "——审计位置装配后不得改写。"
                )
            return _audit_logger
        _audit_logger = JSONLEventLogger(log_dir=log_dir)
        return _audit_logger


def get_audit_logger() -> JSONLEventLogger:
    """取用装配好的审计 logger；未初始化即拒绝——无审计不运行。"""
    if _audit_logger is None:
        raise RuntimeError(
            "审计 logger 未初始化：入口须先 init_audit_logger(log_dir)"
            "（无审计不运行）"
        )
    return _audit_logger


def current_audit_log_dir() -> str | None:
    """当前审计落点（未初始化为 None）。

    给"锚定与否先查再算"的装配方用（基准整场锚一次）：落点来自
    WorkspaceConfig.from_env() 时先查再算,未锚定的进程才付读 env 的代价。
    """
    return None if _audit_logger is None else _audit_logger.log_dir
