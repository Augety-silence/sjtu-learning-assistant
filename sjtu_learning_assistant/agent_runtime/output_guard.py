"""在公开回复进入自动续写前检测密集重复；代码块不参与文本检测。"""
from __future__ import annotations

from collections import Counter
import re


class ModelOutputLoopInterrupt(RuntimeError):
    """中止本次重复流，保留付费片段并允许一次有界纠正。"""


def has_output_loop(text: str) -> bool:
    # 有界窗口，避免逐 token 扫描整篇长文；先排除可能合法重复的代码。
    text = re.sub(r"```[^\n]*\n[\s\S]*?(?:```|$)", "", text)[-6000:]
    text = re.sub(r"\b[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4,12}){2,}\b", "<id>", text)
    lines = [re.sub(r"\s+", "", line) for line in re.split(r"[。！？\n]+", text)]
    if any(count >= 4 for line, count in Counter(lines).items() if len(line) >= 24):
        return True
    paragraphs = [re.sub(r"\s+", "", part) for part in text.split("\n\n")]
    if any(count >= 3 for part, count in Counter(paragraphs).items() if len(part) >= 60):
        return True
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 1600:
        return False
    windows = [compact[i:i + 48] for i in range(0, len(compact) - 48, 8)]
    return bool(windows) and len(set(windows)) / len(windows) < 0.2
