"""进程内查询并发闸（change multi-session-concurrency，design D1/D6）

限制同时在飞的 /query 图执行数，超额 FIFO 排队、可观测、支持排队超时。
挂在 /query endpoint 边界，**不改动 graph / LLMClient 内部结构**（D1）。

为什么用 asyncio.Condition 而非 asyncio.Semaphore：
- 原生暴露 `waiting` 计数（/health 可观测需求）；Semaphore 无此公开 API。
- `notify(1)` 唤醒最早等待者 -> FIFO 公平（spec 要求）；Semaphore 在 3.10+ 也 FIFO，
  但需额外手维护 waiting 计数。
- 排队超时取消时**无 permit 泄漏竞态**：`_in_flight` 仅在 wait 成功后于锁内自增，
  超时/取消返回 False 不自增，故无需 release（asyncio.Semaphore + wait_for 在旧版本
  有取消即泄漏 permit 的竞态，Condition 方案从结构上规避）。

依赖 D2 不变量：单条查询内部 LLM 调用串行（图线性链、候选单次生成、全 src 无内部并行）
-> 请求级并发 ≈ LLM 级并发（1:1），故请求级闸等价 LLM 级闸。
若未来引入查询内并行，须补 LLM 级 `threading.Semaphore` 兜底。

单 worker 部署下进程单例；多 worker 需引入 Redis（非本变更范围，见 run_api.py 注释）。
"""

import asyncio
from typing import Optional

from loguru import logger


class ConcurrencyGate:
    """请求级并发闸：限同时在飞的查询数，超额 FIFO 排队，可观测，支持排队超时。

    线程模型：仅在 asyncio 事件循环内使用（单 worker）。所有 acquire/release 共享
    同一 Condition，状态变更均在锁内，无需额外线程锁。
    """

    def __init__(self, max_concurrency: int, queue_timeout: float):
        self._max = max(1, int(max_concurrency))
        self._queue_timeout = float(queue_timeout)
        self._in_flight = 0      # 已获槽位、正在执行的查询数
        self._waiting = 0        # 正在排队等待槽位的查询数
        self._cond = asyncio.Condition()

    @property
    def queue_timeout(self) -> float:
        """排队超时阈值（秒），供调用方读取。"""
        return self._queue_timeout

    async def try_acquire(self) -> bool:
        """非阻塞获取：有空槽立即占用并返回 True；已满返回 False（不排队、不计数 waiting）。

        用于调用方判断是否需要推送 `queued` 事件：try_acquire 失败 -> 排队态。
        """
        async with self._cond:
            if self._in_flight < self._max:
                self._in_flight += 1
                return True
            return False

    async def acquire(self, timeout: Optional[float] = None) -> bool:
        """阻塞获取槽位，超时返回 False。

        Args:
            timeout: None 用构造时 queue_timeout；传值则覆盖。

        Returns:
            True  -> 成功获取槽位（**必须**配 release() 释放）。
            False -> 排队超时未获取（**不要** release，未占用槽位）。
        """
        to = self._queue_timeout if timeout is None else float(timeout)
        async with self._cond:
            self._waiting += 1
            try:
                # 快路径：当下就有空槽
                if self._in_flight < self._max:
                    self._in_flight += 1
                    return True
                # 排队等待：wait_for 谓词在槽位可用时返回；超时则取消等待（无泄漏）
                try:
                    await asyncio.wait_for(
                        self._cond.wait_for(self._slot_available), timeout=to
                    )
                except asyncio.TimeoutError:
                    return False
                self._in_flight += 1
                return True
            finally:
                self._waiting -= 1

    def _slot_available(self) -> bool:
        return self._in_flight < self._max

    async def release(self) -> None:
        """释放槽位并唤醒一个排队者（FIFO）。多次释放防御性不跌破 0。"""
        async with self._cond:
            if self._in_flight > 0:
                self._in_flight -= 1
            self._cond.notify(1)

    def stats(self) -> dict:
        """并发闸状态快照（供 /health 暴露）。"""
        return {
            "in_flight": self._in_flight,
            "waiting": self._waiting,
            "max": self._max,
            "queue_timeout": self._queue_timeout,
        }
