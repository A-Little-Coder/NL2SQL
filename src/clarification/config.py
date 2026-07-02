# ============================================================================
# Clarification 配置加载（决策 9-15）
# ============================================================================
# 从 config/clarification.yaml 加载反问相关配置，供 TaskPlanner / DialogManager 使用。
# 加载失败时使用默认值，不阻塞主流程。
# ============================================================================

from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# 默认配置（与 config/clarification.yaml 保持一致）
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "max_clarify_rounds": 5,
    "decline_keywords": ["不知道", "跳过", "算了", "skip", "不清楚", "随便"],
}

# 配置文件路径（项目根目录下的 config/clarification.yaml）
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "clarification.yaml"

# 单例缓存
_cached_config: Dict[str, Any] | None = None


def load_clarification_config(force_reload: bool = False) -> Dict[str, Any]:
    """加载 clarification 配置。

    Args:
        force_reload: True 时强制重新读取文件（忽略缓存）

    Returns:
        配置 dict，至少包含 enabled / max_clarify_rounds / decline_keywords
    """
    global _cached_config
    if _cached_config is not None and not force_reload:
        return _cached_config

    config = dict(DEFAULT_CONFIG)

    if yaml is None:
        logger.warning("pyyaml 未安装，使用 clarification 默认配置")
        _cached_config = config
        return config

    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}
            # 合并：文件值覆盖默认值
            for key, default_val in DEFAULT_CONFIG.items():
                if key in file_config and file_config[key] is not None:
                    config[key] = file_config[key]
            logger.debug(f"加载 clarification 配置: {config}")
        else:
            logger.debug(f"clarification 配置文件不存在，使用默认值: {_CONFIG_PATH}")
    except Exception as e:
        logger.warning(f"加载 clarification 配置失败，使用默认值: {e}")

    _cached_config = config
    return config


def get_decline_keywords() -> List[str]:
    """便捷取拒答关键词列表。"""
    cfg = load_clarification_config()
    kws = cfg.get("decline_keywords", DEFAULT_CONFIG["decline_keywords"])
    return list(kws) if kws else list(DEFAULT_CONFIG["decline_keywords"])


def get_max_rounds() -> int:
    """便捷取反问上限。"""
    cfg = load_clarification_config()
    try:
        return int(cfg.get("max_clarify_rounds", DEFAULT_CONFIG["max_clarify_rounds"]))
    except (TypeError, ValueError):
        return DEFAULT_CONFIG["max_clarify_rounds"]


def is_enabled() -> bool:
    """task_planner 是否启用。"""
    cfg = load_clarification_config()
    return bool(cfg.get("enabled", True))
