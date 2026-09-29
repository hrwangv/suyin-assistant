"""PostgreSQL 连接配置（Agent 执行记录；检查点也可以复用同一条连接串）。

配置写在 `.env`（照抄到 .env.example 里的模板）：

    # 方式一：整条连接串（优先，写好这个就不用管下面的分项）
    PG_DSN=postgresql://user:password@127.0.0.1:5432/suyin_agent

    # 方式二：分项配置（密码里带特殊字符时会被自动转义）
    PG_HOST=127.0.0.1
    PG_PORT=5432
    PG_USER=postgres
    PG_PASSWORD=
    PG_DATABASE=suyin_agent

没配 `configured=False`：执行记录降级成"只写日志不落库"，本地开发不用装数据库。
"""
import os
from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class PgConfig:
    """PostgreSQL 连接信息。"""

    dsn: str          # 直接给的整条连接串（为空时按下面的分项拼）
    host: str
    port: int
    user: str
    password: str
    database: str
    schema: str       # 建表用的 schema，默认 public

    @property
    def configured(self) -> bool:
        """能否拼出可用连接串。"""
        return bool(self.connection_string)

    @property
    def connection_string(self) -> str:
        if self.dsn:
            return self.dsn
        if not (self.host and self.user and self.database):
            return ""
        auth = quote_plus(self.user)
        if self.password:
            auth = f"{auth}:{quote_plus(self.password)}"
        return f"postgresql://{auth}@{self.host}:{self.port}/{self.database}"

    def describe(self) -> str:
        """脱敏描述（日志用，不含密码）。"""
        if not self.configured:
            return "未配置"
        if self.dsn:
            return "PG_DSN（已配置）"
        return f"{self.user}@{self.host}:{self.port}/{self.database}"


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


pg_config = PgConfig(
    dsn=(os.getenv("PG_DSN") or "").strip(),
    host=(os.getenv("PG_HOST") or "127.0.0.1").strip(),
    port=_int(os.getenv("PG_PORT", ""), 5432),
    user=(os.getenv("PG_USER") or "").strip(),
    password=os.getenv("PG_PASSWORD") or "",
    database=(os.getenv("PG_DATABASE") or "").strip(),
    schema=(os.getenv("PG_SCHEMA") or "public").strip() or "public",
)
