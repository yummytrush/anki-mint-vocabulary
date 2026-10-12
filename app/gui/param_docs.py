"""高级设置逐参数白话说明 — 标签/悬停文案与 AppConfig 字段的映射表。

同时是 README 参数表格的数据来源（docs 生成时可遍历 PARAM_DOCS）。
文案本体在 strings.py，这里只做 key → (label_key, tip_key, 控件规格) 的组装。
"""
from app.gui import strings as S

# 控件规格类型：check / spin / combo / line / hours（hours = 界面小时 ↔ 存储秒）
PARAM_DOCS: dict[str, dict] = {
    "quality_checks_enabled": {
        "label": S.LABEL_QUALITY_CHECKS, "tip": S.TIP_QUALITY_CHECKS,
        "kind": "check",
    },
    "quality_max_attempts": {
        "label": S.LABEL_QUALITY_ATTEMPTS, "tip": S.TIP_QUALITY_ATTEMPTS,
        "kind": "spin", "min": 0, "max": 5,
    },
    "importance_min_level": {
        "label": S.LABEL_IMPORTANCE_MIN, "tip": S.TIP_IMPORTANCE_MIN,
        "kind": "combo", "options": ["极高", "高", "中", "低"],
    },
    "replace_all": {
        "label": S.LABEL_REPLACE_ALL, "tip": S.TIP_REPLACE_ALL,
        "kind": "check",
    },
    "audio_enabled": {
        "label": S.LABEL_AUDIO_ENABLED, "tip": S.TIP_AUDIO_ENABLED,
        "kind": "check",
    },
    "tts_provider": {
        "label": S.LABEL_TTS_PROVIDER, "tip": S.TIP_TTS_PROVIDER,
        "kind": "combo", "options": ["auto", "gtts", "edge", "none"],
    },
    "tts_fallbacks": {
        "label": S.LABEL_TTS_FALLBACKS, "tip": S.TIP_TTS_FALLBACKS,
        "kind": "line",
    },
    "tts_voice": {
        "label": S.LABEL_TTS_VOICE, "tip": S.TIP_TTS_VOICE,
        "kind": "line",
    },
    "audio_proxy": {
        "label": S.LABEL_AUDIO_PROXY, "tip": S.TIP_AUDIO_PROXY,
        "kind": "line", "placeholder": "http://127.0.0.1:7890",
    },
    "audio_timeout": {
        "label": S.LABEL_AUDIO_TIMEOUT, "tip": S.TIP_AUDIO_TIMEOUT,
        "kind": "spin", "min": 2, "max": 60,
    },
    "audio_fail_cooldown": {
        "label": S.LABEL_AUDIO_COOLDOWN, "tip": S.TIP_AUDIO_COOLDOWN,
        "kind": "hours", "min": 0, "max": 168,
    },
}

# 高级设置页展示顺序（分组标题 → 键列表）
PARAM_GROUPS: list[tuple[str, list[str]]] = [
    ("富化质量", ["quality_checks_enabled", "quality_max_attempts",
                  "importance_min_level", "replace_all"]),
    ("音频与发音", ["audio_enabled", "tts_provider", "tts_fallbacks",
                    "tts_voice", "audio_proxy", "audio_timeout",
                    "audio_fail_cooldown"]),
]
