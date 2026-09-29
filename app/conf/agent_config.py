"""企业业务智能 Agent 的配置。

约定与项目其它 conf 模块一致：只读环境变量，不在导入期建立任何连接。
所有开关都有默认值，保证「不配置也能起服务」（未配置的 MCP 会走降级分支）。

注意：MCP 的连接信息（地址 / 鉴权头 / 工具名 / 入参形态）不在本文件，
统一写在 `app/agent/mcp/builtin.py`；这里只放 Agent 自身的行为开关。
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.utils.path_util import PROJECT_ROOT

load_dotenv()


def _flag(name: str, default: str = "true") -> bool:
    """布尔开关：'0/false/no/off' 视为关闭。"""
    return str(os.getenv(name, default)).strip().lower() not in {"0", "false", "no", "off", ""}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _path(name: str, default: str) -> Path:
    raw = os.getenv(name) or default
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class AgentConfig:
    """Main Agent（企业业务智能 Agent）运行参数。"""

    # 是否挂载 /api/agent/* 路由。关闭时后端行为与改造前完全一致。
    enabled: bool
    # Main Agent 使用的文本模型；留空走项目默认 LLM_MODEL_ID
    llm_model: str | None
    # 一次用户请求内，Supervisor 最多决策几次（防止 document → supervisor 来回打转）
    max_supervisor_turns: int

    # ---------- Document Skill ----------
    # 「只上传询证函、用户没说要做什么」时是否自动提取邮寄地址。
    # 留空 = 不自动执行，按规范要求反问用户（规范第 21 节）。
    default_inquiry_action: str
    # 文档正文送进大模型的字符上限（分类/抽取/摘要共用）
    document_max_chars: int

    # ---------- Application Skill ----------
    # 多候选企业是否需要用户确认；False 时取最高分
    company_require_confirmation: bool
    # Company MCP 单次返回的候选上限
    company_search_limit: int
    # 生成 DOCX 前是否需要用户确认
    require_confirm_before_render: bool
    # 缺字段时最多让用户补充几轮
    max_field_ask_rounds: int
    # DOCX 渲染器：auto / docxtpl / python-docx
    docx_renderer: str

    # ---------- 目录与文件 ----------
    templates_dir: Path
    outputs_dir: Path
    files_dir: Path
    # 附件下载地址前缀（前端拼 url 用）
    file_url_prefix: str
    # 单个上传附件大小上限（MB）
    max_upload_mb: int

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


agent_config = AgentConfig(
    enabled=_flag("AGENT_ENABLED", "true"),
    llm_model=os.getenv("AGENT_LLM_MODEL_ID") or None,
    max_supervisor_turns=_int("AGENT_MAX_SUPERVISOR_TURNS", 4),
    default_inquiry_action=(os.getenv("AGENT_DEFAULT_INQUIRY_ACTION") or "").strip(),
    document_max_chars=_int("AGENT_DOCUMENT_MAX_CHARS", 12000),
    company_require_confirmation=_flag("AGENT_COMPANY_REQUIRE_CONFIRMATION", "true"),
    company_search_limit=_int("AGENT_COMPANY_SEARCH_LIMIT", 10),
    require_confirm_before_render=_flag("AGENT_REQUIRE_CONFIRM_BEFORE_RENDER", "true"),
    max_field_ask_rounds=_int("AGENT_MAX_FIELD_ASK_ROUNDS", 2),
    docx_renderer=(os.getenv("AGENT_DOCX_RENDERER") or "auto").strip().lower(),
    templates_dir=_path("AGENT_TEMPLATE_DIR", "templates"),
    outputs_dir=_path("AGENT_OUTPUT_DIR", "outputs"),
    files_dir=_path("AGENT_FILE_DIR", "output/agent_files"),
    file_url_prefix=(os.getenv("AGENT_FILE_URL_PREFIX") or "/api/agent/files").rstrip("/"),
    max_upload_mb=_int("AGENT_MAX_UPLOAD_MB", 20),
)
