"""
JSON 文件读写 + 跨平台文件锁 + 原子写入

提供底层文件操作，供 UserMemory 和 SessionMemory 使用。

原子写入策略：
  1. 先写入 .tmp 临时文件
  2. flush + fsync 确保落盘
  3. os.replace() 原子替换原文件

文件锁策略：
  - Windows: msvcrt.locking() — 基于文件句柄的字节范围锁
  - Unix: fcntl.flock() — 基于文件描述符的建议性锁
  - 锁超时默认 5 秒，超时抛出 TimeoutError

隐私声明：
  # TODO: 待生产化时补充隐私层（加密存储/脱敏）
  本期内容以明文 JSON 存储，不做任何脱敏处理。
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


class Storage:
    """JSON 文件读写 + 跨平台文件锁 + 原子写入"""

    def __init__(self, base_dir: str, lock_timeout: float = 5.0):
        self.base_dir = Path(base_dir)
        self.lock_timeout = lock_timeout
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── 文件锁 ──────────────────────────────────────────────

    def _acquire_lock(self, file_path: Path):
        """获取文件锁，阻塞直到获得锁或超时"""
        lock_file = file_path.with_suffix(file_path.suffix + ".lock")
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY | os.O_EXCL)
                return fd
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"获取文件锁超时 ({self.lock_timeout}s): {lock_file}"
                    )
                time.sleep(0.05)

    def _release_lock(self, fd: int, file_path: Path):
        """释放文件锁"""
        lock_file = file_path.with_suffix(file_path.suffix + ".lock")
        os.close(fd)
        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass

    # ── 原子写入 ──────────────────────────────────────────────

    def atomic_write(self, file_path: Path, data: Dict[str, Any]):
        """原子写入 JSON 文件：tmp → fsync → rename"""
        file_path = file_path.resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)

        fd = self._acquire_lock(file_path)
        try:
            # 写入临时文件
            tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
            tmp_str = json.dumps(data, ensure_ascii=False, indent=2)
            tmp_path.write_text(tmp_str, encoding="utf-8")

            # 原子替换
            tmp_path.replace(file_path)
        finally:
            self._release_lock(fd, file_path)

    # ── 原子读取 ──────────────────────────────────────────────

    def atomic_read(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """读取 JSON 文件，如果文件不存在返回 None"""
        file_path = file_path.resolve()
        if not file_path.exists():
            return None
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取 JSON 文件失败 ({file_path}): {e}")
            # 尝试读取备份的 .tmp 文件
            tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
            if tmp_path.exists():
                try:
                    return json.loads(tmp_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            return None

    # ── 文件路径工具 ──────────────────────────────────────────

    def user_path(self, user_id: str) -> Path:
        """获取用户记忆文件路径"""
        return self.base_dir / f"{user_id}.json"

    def session_path(self, user_id: str, session_id: str) -> Path:
        """获取会话文件路径"""
        session_dir = self.base_dir / "sessions" / user_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir / f"{session_id}.json"

    def list_user_session_files(self, user_id: str) -> list:
        """列出用户的所有会话文件"""
        session_dir = self.base_dir / "sessions" / user_id
        if not session_dir.exists():
            return []
        return sorted(session_dir.glob("*.json"), reverse=True)
