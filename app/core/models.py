"""数据结构 — Importance / WordEntry 等纯数据定义（原文搬运）。"""

from dataclasses import dataclass
from enum import Enum


# ===========================================================================
# 数据结构
# ===========================================================================
class Importance(Enum):
    """考试重要性等级 — 数值越大越重要"""
    EXTREME_HIGH = "极高"
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"

    @classmethod
    def from_str(cls, s: str) -> "Importance":
        for m in cls:
            if m.value == s:
                return m
        return cls.MEDIUM  # 默认中等

    def to_level(self) -> int:
        """转为整数以便比较"""
        return {self.EXTREME_HIGH: 4, self.HIGH: 3, self.MEDIUM: 2, self.LOW: 1}[self]

    def meets_minimum(self, min_str: str) -> bool:
        """是否满足最低重要性要求"""
        try:
            min_imp = Importance.from_str(min_str)
            return self.to_level() >= min_imp.to_level()
        except Exception:
            return True


@dataclass
class WordEntry:
    """Markdown 解析后的单词条目"""
    index: int          # 序号
    word: str           # 单词/词组，保持原样
    sentence: str       # 完整例句


@dataclass
class ProcessedRecord:
    """已处理单词记录"""
    word: str
    sentence_hash: str
    processed_at: str


@dataclass
class AudioHealth:
    """TTS provider 可用性预检结果。"""
    available: bool
    provider: str
    error: str = ""


@dataclass
class AudioResult:
    """单词音频生成结果。"""
    ok: bool
    provider: str
    filename: str
    error: str = ""
