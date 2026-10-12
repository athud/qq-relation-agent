from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
load_dotenv()


def _env_str(k, default=""):
    v = os.environ.get(k)
    return v if v not in (None, "") else default


def _env_int(k, default):
    v = os.environ.get(k)
    try:
        return int(v) if v else default
    except ValueError:
        return default


def _env_float(k, default):
    v = os.environ.get(k)
    try:
        return float(v) if v else default
    except ValueError:
        return default


def _env_bool(k, default):
    v = os.environ.get(k)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def _env_groups():
    raw = os.environ.get("QQ_GROUPS", "")
    return [int(x) for x in raw.split(",") if x.strip().isdigit()]


@dataclass
class Config:
    # ==================== OneBot 连接 ====================
    onebot_url: str = field(default_factory=lambda: _env_str(
        "ONEBOT_URL", "ws://127.0.0.1:3001"))
    onebot_token: str = field(default_factory=lambda: _env_str(
        "ONEBOT_TOKEN", ""))

    # 留空 = 自动监控所有收到消息的群
    groups: list = field(default_factory=_env_groups)

    # 屏蔽名单：这些昵称不会出现在关系图、人格画像、报告里
    # 逗号分隔，环境变量 IGNORE_NAMES 设置
    ignore_names: set = field(default_factory=lambda: {
        x.strip() for x in os.environ.get("IGNORE_NAMES", "").split(",")
        if x.strip()
    })


    # ===================== 分析节奏 =====================
    interval_seconds: int = field(default_factory=lambda: _env_int(
        "ANALYZE_INTERVAL_SEC", 6 * 3600))
    # 服务启动后先等多久再跑第一轮（等 OneBot 连上）
    analysis_startup_delay: int = field(default_factory=lambda: _env_int(
        "ANALYSIS_STARTUP_DELAY", 30))

    # ===================== 采样与规模限制 =====================
    min_messages: int = field(default_factory=lambda: _env_int(
        "MIN_MESSAGES", 50))                          # 群增量少于这个数跳过
    min_user_messages: int = field(default_factory=lambda: _env_int(
        "MIN_USER_MESSAGES", 20))                     # 个人少于这个数不做人格
    persona_min_messages_soft: int = field(default_factory=lambda: _env_int(
        "PERSONA_MIN_MESSAGES_SOFT", 30))             # 提示词里"太少"的软阈值
    max_messages_per_analysis: int = field(default_factory=lambda: _env_int(
        "MAX_MESSAGES_PER_ANALYSIS", 1500))
    max_transcript_chars: int = field(default_factory=lambda: _env_int(
        "MAX_TRANSCRIPT_CHARS", 40000))
    max_personas_per_run: int = field(default_factory=lambda: _env_int(
        "MAX_PERSONAS_PER_RUN", 30))
    max_user_samples: int = field(default_factory=lambda: _env_int(
        "MAX_USER_SAMPLES", 200))
    members_list_limit: int = field(default_factory=lambda: _env_int(
        "MEMBERS_LIST_LIMIT", 300))

    # ===================== 关系结果过滤 =====================
    # 低于此置信度的隐含关系直接丢弃（不再进报告/推送/图谱）
    min_implicit_confidence: float = field(default_factory=lambda: _env_float(
        "MIN_IMPLICIT_CONFIDENCE", 0.5))
    # 提示词里"每条关系最多几条 evidence"
    evidence_max_per_relation: int = field(default_factory=lambda: _env_int(
        "EVIDENCE_MAX_PER_RELATION", 2))
    # to_text 里每条关系展示几条 evidence
    evidence_display_max: int = field(default_factory=lambda: _env_int(
        "EVIDENCE_DISPLAY_MAX", 3))

    # ===================== LLM =====================
    api_key: str = field(default_factory=lambda: _env_str(
        "DEEPSEEK_API_KEY") or _env_str("OPENAI_API_KEY"))
    base_url: str = field(default_factory=lambda: _env_str(
        "LLM_BASE_URL", "https://api.deepseek.com"))
    model: str = field(default_factory=lambda: _env_str(
        "LLM_MODEL", "deepseek-chat"))
    temperature: float = field(default_factory=lambda: _env_float(
        "LLM_TEMPERATURE", 0.2))
    llm_concurrency: int = field(default_factory=lambda: _env_int(
        "LLM_CONCURRENCY", 3))
    llm_timeout: int = field(default_factory=lambda: _env_int(
        "LLM_TIMEOUT", 180))

    # ===================== 输出 =====================
    data_dir: str = field(default_factory=lambda: _env_str("DATA_DIR", "data"))
    push_to_group: bool = field(default_factory=lambda: _env_bool(
        "PUSH_TO_GROUP", True))
    push_max_chars: int = field(default_factory=lambda: _env_int(
        "PUSH_MAX_CHARS", 1500))
    push_top_relations: int = field(default_factory=lambda: _env_int(
        "PUSH_TOP_RELATIONS", 5))
    push_top_personas: int = field(default_factory=lambda: _env_int(
        "PUSH_TOP_PERSONAS", 8))
        # 是否额外生成自包含 HTML（头像和 vis-network 都内嵌，单文件可发人）
    embed_html: bool = field(default_factory=lambda: _env_bool("EMBED_HTML", False))


def load_config() -> Config:
    return Config()