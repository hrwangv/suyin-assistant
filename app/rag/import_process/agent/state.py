from typing import TypedDict, Any
import copy
from app.core.logger import logger


# ==================== state 日志摘要工具 ====================

# 需要截断的字符串字段及保留长度
_LARGE_STR_FIELDS: dict[str, int] = {
    "md_content": 120,
    "local_dir": 80,
    "input_file_path": 80,
    "other_path": 80,
    "md_path": 80,
    "split_path": 80,
    "embeddings_path": 80,
}

# 需要隐藏内容、只显示元素个数的向量字段
_VECTOR_FIELDS = {"dense_vector", "sparse_vector"}

# 列表/字典类字段最多展示的元素数
_MAX_PREVIEW_ITEMS = 3

# 普通字符串字段的默认截断长度
_DEFAULT_MAX_STR = 80


def _trim_val(val: Any, max_len: int = _DEFAULT_MAX_STR) -> Any:
    """递归精简任意值：字符串截断，向量显示长度，列表/字典只展示前 N 个元素"""
    if isinstance(val, str):
        return val[:max_len] + ("..." if len(val) > max_len else "")
    if isinstance(val, dict):
        trimmed = {}
        for k, v in val.items():
            if k in _VECTOR_FIELDS:
                n = len(v) if isinstance(v, (list, dict)) else "?"
                trimmed[k] = f"<{n} elements>"
            else:
                trimmed[k] = _trim_val(v, max_len)
        return trimmed
    if isinstance(val, list):
        if len(val) <= _MAX_PREVIEW_ITEMS:
            return [_trim_val(v, max_len) for v in val]
        return [_trim_val(v, max_len) for v in val[:_MAX_PREVIEW_ITEMS]] + [
            f"... 共 {len(val)} 项"
        ]
    return val


def state_summary(state: dict, max_str_len: int = _DEFAULT_MAX_STR) -> dict:
    """返回 state 的摘要版，截断大文本和向量字段，避免日志爆炸。

    规则：
    - md_content 等大文本字段截断到 _LARGE_STR_FIELDS 指定的长度
    - dense_vector / sparse_vector 替换为 ``<1024 elements>`` 等长度标记
    - chunks 等列表只展示前 3 个元素的摘要，其余显示总数
    """
    summary: dict[str, Any] = {}
    for k, v in state.items():
        limit = _LARGE_STR_FIELDS.get(k, max_str_len)
        summary[k] = _trim_val(v, limit)
    return summary



'''
定义图的初始状态，也就是一个图节点里需要有哪些字段
'''


class ImportGraphState(TypedDict):
    """
    图的状态定义，包含所有节点产生和消费的数据字段。
    TypedDict 让我们在代码中能有自动补全和类型检查。
    使用字典式访问（如state["session_id"]、state.get("embedding_chunks")）
    """
    task_id: str          # 任务唯一ID，用于追踪日志
    file_id: str          # 文件唯一ID，用于 MySQL 元数据与 Qdrant 向量关联

    # --- 流程控制标记 ---
    is_md_read_enabled: bool   # 是否启用 Markdown 读取路径
    is_pdf_read_enabled: bool  # 是否启用 PDF 读取路径



    # --- 切块相关 --- 【没用】
    is_normal_split_enabled: bool
    is_silicon_flow_api_enabled: bool
    is_advanced_split_enabled: bool
    is_vllm_enabled: bool

    # --- 路径相关 ---
    local_dir: str        # 当前工作目录或输出目录
    input_file_path: str  # 原始输入文件路径
    file_title: str       # 文件标题（文件名去后缀）
    other_path: str       # PDF等其他文件路径 (如果输入是PDF或者其他可解析的文件)
    md_path: str          # Markdown 文件路径 (转换后或直接输入的)
    split_path: str       # 分块后的文件路径 【没用】
    embeddings_path: str  # 向量数据库文件路径【没用】


    # --- 内容数据 ---
    md_content: str       # Markdown 的全文内容
    chunks: list          # 切片后的文本列表，包含 metadata
    item_name: str        # 识别出的主体名称 (如: “江苏金租”)，用于增强检索

    news_date:str         # 新闻发布的日期
    industry:str          # 所处行业，如：新能源、人工智能等
    section:str           # 资料领域，如：经营晨报、内部发文等

    # --- 数据库相关 ---
    embeddings_content: list # 包含向量数据的列表，准备写入向量数据库


# 建议定一个初始化对象，方便后续使用
# 定义图状态的默认初始值
graph_default_state: ImportGraphState = {
    "task_id":"",
    "file_id":"",
    "is_pdf_read_enabled": False,
    "is_md_read_enabled": False,
    "is_normal_split_enabled": True,
    "is_silicon_flow_api_enabled": True,
    "is_advanced_split_enabled": False,
    "is_vllm_enabled": False,
    "is_others_read_enabled": False,
    "local_dir": "",
    "input_file_path": "",
    "other_path": "",
    "md_path": "",
    "file_title": "",
    "split_path": "",
    "embeddings_path": "",
    "md_content": "",
    "chunks": [],
    "item_name": "",
    "embeddings_content": [],
    "news_date":"",
    "industry":"",
    "section":""
}

def create_default_state(**overrides) -> ImportGraphState:
    """
    创建默认状态，支持覆盖

    Args:
        **overrides: 要覆盖的字段（关键字参数解包）

    Returns:
        新的状态实例

    Examples:
        state = create_default_state(task_id="task_001", local_file_path="doc.pdf")
    """

    # 默认状态
    state = copy.deepcopy(graph_default_state)
    # 用 overrides 覆盖默认值
    state.update(overrides)
    # 返回创建好的状态字典实例
    return state

def get_default_state() -> ImportGraphState:
    """
    返回一个新的默认状态实例，避免全局变量污染
    """
    return copy.deepcopy(graph_default_state)


if __name__ == "__main__":
    """
    测试
    """
    # 创建默认状态
    state = create_default_state(input_file_path="万用表RS-12的使用.pdf")
    logger.info(state)
