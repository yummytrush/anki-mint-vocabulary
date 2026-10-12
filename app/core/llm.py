"""LLM API 调用层 — OpenAI 兼容接口、JSON 稳健修复、速率限制（原文搬运）。

跨模块约定：其它模块一律以 `llm.llm_chat(...)` 形式调用，保证测试
monkeypatch `llm.llm_chat` 时所有调用点（富化/质检/修复）同时生效。
"""

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from app.core import config

logger = logging.getLogger(__name__)

try:
    from openai import AsyncOpenAI, OpenAI
except ImportError:
    print("[ERROR] openai 未安装。fix: pip install openai")
    import sys
    sys.exit(1)


# ===========================================================================
# 3. LLM API 调用
# ===========================================================================
def repair_and_parse_json(content: str) -> dict:
    """
    从 LLM 原始响应中稳健地解析 JSON 对象，带多级修复。

    修复策略（按顺序尝试，任一成功即返回）：
      1. 剥离 markdown 围栏（```json ... ```）、BOM、零宽字符
      2. 定位首个 '{'，丢弃其前的解释性文字
      3. 直接 json.loads
      4. 尾部截断修复：从末尾向前找最后一个完整的 '}'，截断重试
      5. 括号配平：补齐缺失的 '}' 和 ']'，剥离尾随逗号后重试

    关键假设：响应里至少包含一个以 '{' 开头的 JSON 对象。
    抛出: json.JSONDecodeError（所有修复都失败时），调用方据此判定该批失败。
    """
    if content is None:
        raise json.JSONDecodeError("内容为空", "", 0)

    # --- 1. 基础清洗 ---
    text = content.strip()
    # 剥离 markdown 围栏
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    # BOM 与零宽字符
    text = text.lstrip("﻿").replace("​", "").strip()

    # --- 2. 定位首个 '{' ---
    brace_idx = text.find("{")
    if brace_idx > 0:
        text = text[brace_idx:]
    elif brace_idx == -1:
        # 完全没有 '{' — 包裹一层后交给后续逻辑
        text = "{" + text + "}"

    # --- 3. 直接解析 ---
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # --- 4. 尾部截断修复：截到最后一个 '}' ---
    last_brace = text.rfind("}")
    if last_brace != -1 and last_brace < len(text) - 1:
        try:
            return json.loads(text[: last_brace + 1])
        except json.JSONDecodeError:
            pass

    # --- 5. 括号配平 + 尾逗号清理 ---
    repaired = _balance_braces(text)
    return json.loads(repaired)  # 失败则抛 JSONDecodeError 给调用方


def _balance_braces(text: str) -> str:
    """
    括号配平辅助：补齐截断响应缺失的 '}' / ']'，并剥离尾随逗号。

    扫描时跳过字符串字面量内的括号（处理转义引号），统计未闭合的
    '{' 和 '['，在末尾按正确顺序补齐。常见于 max_tokens 耗尽导致的截断。
    """
    stack: list[str] = []
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    repaired = text
    # 若停在字符串中途，先补一个引号闭合
    if in_string:
        repaired += '"'
    # 剥离结尾的逗号/空白（截断常留下 "...,\n"）
    repaired = re.sub(r",\s*$", "", repaired.rstrip())
    # 按栈逆序补齐闭合符
    for opener in reversed(stack):
        # 闭合前也要清掉紧邻的尾逗号
        repaired = re.sub(r",\s*$", "", repaired.rstrip())
        repaired += "}" if opener == "{" else "]"
    return repaired


# 速率限制器：用滑动窗口记录最近 N 次请求的时间戳
class RateLimiter:
    """
    滑动窗口速率限制器。
    每次请求前调用 acquire()，自动等待直到速率允许。
    """

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                # 移除 60 秒前的记录
                while self._timestamps and now - self._timestamps[0] > 60:
                    self._timestamps.popleft()
                # 若未达上限，记录并返回
                if len(self._timestamps) < self.max_per_minute:
                    self._timestamps.append(time.monotonic())
                    return
                wait_time = 60 - (now - self._timestamps[0]) + 0.1
                logger.debug(f"速率等待 {wait_time:.1f}s")
            await asyncio.sleep(wait_time)


rate_limiter = RateLimiter(config.RATE_LIMIT_PER_MIN)


def get_client() -> AsyncOpenAI:
    """创建异步 OpenAI 客户端实例。"""
    if not config.LLM_API_KEY:
        raise RuntimeError(
            "LLM_API_KEY 未设置。请在 .env 文件中设置或通过环境变量导出。"
        )
    return AsyncOpenAI(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        timeout=120.0,   # DeepSeek 慢响应需要较长超时
        max_retries=0,    # 禁用 SDK 内建重试，由我们自己的重试逻辑控制
    )


async def llm_chat(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    sem: Optional[asyncio.Semaphore] = None,
) -> Optional[dict]:
    """
    调用 LLM Chat Completion，带重试 + 并发 + 速率控制。

    参数:
      messages: OpenAI 格式的消息列表
      temperature: 采样温度
      max_tokens: 最大生成 token 数
      sem: 并发信号量（全局共享）

    返回值: 解析后的 JSON dict，失败返回 None
    """
    if sem is None:
        sem = asyncio.Semaphore(config.MAX_CONCURRENCY)

    async with sem:
        for attempt in range(3):
            try:
                await rate_limiter.acquire()

                client = get_client()

                # 注意：DeepSeek 不支持 response_format 参数；在 prompt 中要求纯 JSON
                extra_kwargs = {}
                if "deepseek" not in config.LLM_BASE_URL.lower():
                    extra_kwargs["response_format"] = {"type": "json_object"}

                response = await client.chat.completions.create(
                    model=config.LLM_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **extra_kwargs,
                )

                content = response.choices[0].message.content
                if content is None:
                    raise ValueError("LLM 返回空内容")

                # 统一交给稳健解析器：剥离围栏/BOM、定位首括号、
                # 尾部截断修复、括号配平（详见 repair_and_parse_json）
                return repair_and_parse_json(content)

            except json.JSONDecodeError as e:
                # 记录原始响应以便调试（截断）
                raw_snippet = content[:200] if content else "(empty)"
                if attempt < 2:
                    logger.warning(
                        f"JSON 解析失败 (尝试 {attempt + 1}/3): {e} | raw: {raw_snippet}"
                    )
                    await asyncio.sleep(1)
                else:
                    logger.error(
                        f"JSON 解析最终失败: {e} | raw: {raw_snippet}"
                    )
                    return None
            except Exception as e:
                status = getattr(e, "status_code", None)
                if status == 429:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(
                        f"速率限制 (429)，重试 {attempt + 1}/3，等待 {wait}s"
                    )
                    await asyncio.sleep(wait)
                elif attempt < 2:
                    logger.warning(
                        f"LLM 调用失败 (尝试 {attempt + 1}/3): {e}"
                    )
                    await asyncio.sleep(1)
                else:
                    logger.error(
                        f"LLM 调用最终失败 (已重试 3 次): {e}"
                    )
                    return None

    return None


# ===========================================================================
# 连通测试（Task 3） — GUI「测试连通」与 CLI 共用
# ===========================================================================
@dataclass
class ConnectionResult:
    """连通测试结果。error_class: ""（成功）| network | auth | model_not_found | other"""
    ok: bool
    latency_ms: int = 0
    error_class: str = ""
    message: str = ""


def _classify_connection_error(e: Exception) -> tuple[str, str]:
    """把 openai SDK 异常归类为 (error_class, 白话建议)。"""
    import openai as _openai

    status = getattr(e, "status_code", None)
    if isinstance(e, _openai.APITimeoutError):
        return "network", "连接超时：请检查网络或代理设置。"
    if isinstance(e, _openai.APIConnectionError):
        return "network", "网络不通：无法连接到 API 服务器，请检查网络/代理。"
    if isinstance(e, _openai.AuthenticationError) or status in (401, 403):
        return "auth", "鉴权失败：API Key 无效或没有权限（401/403）。"
    if isinstance(e, _openai.NotFoundError) or status == 404:
        return "model_not_found", "接口不存在：Base URL 可能填错，或该端点未开放。"
    if status == 400:
        return "model_not_found", "请求被拒绝(400)：常见原因是模型名不存在，请核对模型名。"
    if status == 429:
        return "other", "触发限流(429)：Key 通常有效，但请求过于频繁或额度不足，可稍后再试。"
    return "other", f"未知错误：{type(e).__name__}"


def test_llm_connection(cfg) -> ConnectionResult:
    """发一次最小对话（「回复 OK」）验证连通性：同步、非流式、8s 超时、max_tokens 极小。

    cfg 只要求具备 api_key / base_url / model 属性（AppConfig 即可）。
    """
    client = OpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        timeout=8.0,
        max_retries=0,
    )
    start = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=cfg.model,
            messages=[{"role": "user", "content": "回复 OK"}],
            temperature=0,
            max_tokens=8,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        content = response.choices[0].message.content
        if not content:
            return ConnectionResult(False, latency_ms, "other", "模型返回空内容，请稍后重试。")
        return ConnectionResult(True, latency_ms, "", f"模型回复正常: {content.strip()[:50]}")
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        error_class, advice = _classify_connection_error(e)
        logger.warning(f"连通测试失败({error_class}): {e}")
        return ConnectionResult(False, latency_ms, error_class, f"{advice}（{e}）")


async def llm_chat_raw(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    sem: Optional[asyncio.Semaphore] = None,
) -> Optional[str]:
    """
    llm_chat 的原始文本版：返回模型输出原文，不做 JSON 解析（Task 4）。

    适用于输出为 Markdown/纯文本的场景（AI 格式规范化、AI 助手对话）。
    重试/并发/速率逻辑与 llm_chat 相同；失败返回 None。
    """
    if sem is None:
        sem = asyncio.Semaphore(config.MAX_CONCURRENCY)

    async with sem:
        for attempt in range(3):
            try:
                await rate_limiter.acquire()

                client = get_client()
                # 非 JSON 输出不强制 response_format
                response = await client.chat.completions.create(
                    model=config.LLM_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                content = response.choices[0].message.content
                if content is None:
                    raise ValueError("LLM 返回空内容")
                return content

            except Exception as e:
                status = getattr(e, "status_code", None)
                if status == 429:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(
                        f"速率限制 (429)，重试 {attempt + 1}/3，等待 {wait}s"
                    )
                    await asyncio.sleep(wait)
                elif attempt < 2:
                    logger.warning(
                        f"LLM 调用失败 (尝试 {attempt + 1}/3): {e}"
                    )
                    await asyncio.sleep(1)
                else:
                    logger.error(
                        f"LLM 调用最终失败 (已重试 3 次): {e}"
                    )
                    return None

    return None


def llm_chat_stream(
    cfg,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
):
    """
    同步流式对话（GUI「AI 助手」用，Task 6）。

    cfg 只要求具备 api_key / base_url / model 属性（建议传 AppConfig 快照，
    不读 config 模块全局，避免与生成流水线共享状态）。
    逐 chunk yield 文本增量；不做 JSON 解析。网络/API 异常直接抛出，
    由调用方（ChatWorker）捕获并展示。
    """
    client = OpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        timeout=60.0,
        max_retries=0,
    )
    stream = client.chat.completions.create(
        model=cfg.model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
