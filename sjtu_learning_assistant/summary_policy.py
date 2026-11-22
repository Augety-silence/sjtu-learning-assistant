"""课件／课堂总结的共同长度目标与可恢复退避；原始资料不裁剪。"""
from __future__ import annotations

import math
import re

MAX_SUMMARY_CHARS = 5000
MAX_MARKDOWN_CHARS = 4850
MAX_SECTION_TEXT_CHARS = 4700
# 缓存身份沿用 v1：放宽长度不使已付费、已审校的旧结果全量失效。
SUMMARY_POLICY = "source-ratio-30-percent-v1; max-readable-3000; retry-backoff-v1"


def source_chars(text: str) -> int:
    """排除提取器页码及空白；不把膨胀的机器解读当作原始篇幅。"""
    text = re.sub(r"【第\s*\d+\s*页\s*/\s*共\s*\d+\s*页】", "", text)
    return len(re.sub(r"\s+", "", text))


def summary_budget(characters: int, *, legacy: bool = False) -> dict:
    # 比例是编辑目标而非机械删字的硬校验；重要公式和条件允许接近上限。
    target = min(2600 if legacy else 4300, max(160, math.ceil(max(0, characters) * 0.30)))
    return {"source_chars": characters, "target_chars": target,
            "target_min_chars": max(80, math.floor(target * 0.75)),
            "target_max_chars": min(2850 if legacy else MAX_MARKDOWN_CHARS, math.ceil(target * 1.10)),
            "max_summary_chars": 3000 if legacy else MAX_SUMMARY_CHARS,
            "policy": SUMMARY_POLICY if legacy else "source-ratio-30-percent-v1; max-readable-5000; retry-backoff-v1"}


def length_request(prompt: str, data: dict) -> tuple[str, dict]:
    """仅升级实际请求；稳定旧检查点键，不改源文、已通过稿或历史响应。"""
    prompt += ("\n本次长度规则更新，覆盖前文旧长度数字：最终阅读正文含标题、引用最多5000字符；"
               "markdown最多4850字符，字幕section正文合计最多4700字符、每段最多1500字符。"
               "长资料目标4300字符，独立压缩安全目标4200字符；短资料按本次source_budget约原文30%收敛，不凑字数。")
    if "source_budget" in data and "source_chars" in data["source_budget"]:
        old = data["source_budget"]
        budget = summary_budget(old["source_chars"])
        # 字幕分块的短事实卡不能各自扩写成一整堂课。
        if old["target_chars"] < summary_budget(old["source_chars"], legacy=True)["target_chars"]:
            budget.update(target_chars=old["target_chars"], target_min_chars=old["target_min_chars"],
                          target_max_chars=old["target_max_chars"])
        data = {**data, "source_budget": budget}
    return prompt, data


def retry_delay_seconds(attempts: int) -> int:
    """连续失败逐步退避到6小时，不因达到3次而永久跳过。"""
    return (300, 900, 2700, 7200, 21600)[min(4, max(0, attempts - 1))]
