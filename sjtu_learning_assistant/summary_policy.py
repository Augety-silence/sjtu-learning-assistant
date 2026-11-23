"""课件／课堂总结的共同长度目标与可恢复退避；原始资料不裁剪。"""
from __future__ import annotations

import hashlib
import math
import re

MAX_SUMMARY_CHARS = 5000
MAX_MARKDOWN_CHARS = 4850
MAX_SECTION_TEXT_CHARS = 4700
# 缓存身份沿用 v1：放宽长度不使已付费、已审校的旧结果全量失效。
SUMMARY_POLICY = "source-ratio-30-percent-v1; max-readable-3000; retry-backoff-v1"

# 分段稿按确定性抽样送全量来源审校；合并稿始终审校，避免抽样后整篇失去来源锚定。
REVIEW_SAMPLE_PERCENT = 20
REVIEW_POLICY = f"sampled-{REVIEW_SAMPLE_PERCENT}-percent; merge-always-reviewed; v1"
SUMMARY_PARALLEL_BATCHES = 3
AUTO_RECOVERY_VERSION = 'bounded-patches-topics-20261123-v2'


def recovery_delay_seconds(attempts: int, error: str = '') -> int:
    """网络恢复快退避；结构恢复限次，不全库清空缓存或每日额度。"""
    if any(word in error.lower() for word in ('超时', 'timeout', 'network', 'connection', '网络', '连接')):
        return (30, 90, 180, 600, 1800)[min(4, max(0, attempts - 1))]
    return (60, 180, 600, 1800)[min(3, max(0, attempts - 1))]


def cache_friendly_data(data: dict) -> dict:
    """原文固定在前，草稿及修复意见在后；不删改内容或检查点身份。"""
    first = ('pages', 'cues', 'input_summaries', 'draft_markdown')
    return {**{key: data[key] for key in first if key in data},
            **{key: data[key] for key in sorted(data) if key not in first}}


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


def sampled_review(identity: str, percent: int = REVIEW_SAMPLE_PERCENT) -> bool:
    """确定性抽样：同一份输入在任何重启、任何机器上都得到同一结论。

    不能用随机数，否则续编会把同一份稿反复送入或不送入审校，既不可复现也浪费额度。
    """
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    value = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16) % 100
    return value < percent
