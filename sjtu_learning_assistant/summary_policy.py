"""课件／课堂总结的共同长度目标与可恢复退避；原始资料不裁剪。"""
from __future__ import annotations

import math
import re

MAX_SUMMARY_CHARS = 3000
MAX_MARKDOWN_CHARS = 2850
MAX_SECTION_TEXT_CHARS = 2700
SUMMARY_POLICY = "source-ratio-30-percent-v1; max-readable-3000; retry-backoff-v1"


def source_chars(text: str) -> int:
    """排除提取器页码及空白；不把膨胀的机器解读当作原始篇幅。"""
    text = re.sub(r"【第\s*\d+\s*页\s*/\s*共\s*\d+\s*页】", "", text)
    return len(re.sub(r"\s+", "", text))


def summary_budget(characters: int) -> dict:
    # 比例是编辑目标而非机械删字的硬校验；重要公式和条件允许接近上限。
    target = min(2600, max(160, math.ceil(max(0, characters) * 0.30)))
    return {"source_chars": characters, "target_chars": target,
            "target_min_chars": max(80, math.floor(target * 0.75)),
            "target_max_chars": min(MAX_MARKDOWN_CHARS, math.ceil(target * 1.10)),
            "max_summary_chars": MAX_SUMMARY_CHARS, "policy": SUMMARY_POLICY}


def retry_delay_seconds(attempts: int) -> int:
    """连续失败逐步退避到6小时，不因达到3次而永久跳过。"""
    return (300, 900, 2700, 7200, 21600)[min(4, max(0, attempts - 1))]
