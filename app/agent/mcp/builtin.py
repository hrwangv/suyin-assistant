"""随代码发布的 MCP 服务声明（MCP 的唯一配置处）。

对照 Yuxi 的 `backend/package/yuxi/agents/mcp/builtin.py`：MCP 的「有哪些服务、
连哪里、用什么传输、暴露哪些工具、怎么传参」全部写在代码里，
`.env` 只保留两样东西：

    1. 密钥：api_key_env 指向的环境变量名（密钥不进仓库）
    2. 地址覆盖（可选）：url_env，本地联调 / CI 里临时换地址用

于是"换厂商、加服务、改工具名、改入参形态"都只改这一个文件：

    BUILTIN_MCP_SERVERS = {
        "slug": {
            "name": 展示名,
            "description": 一句话说明（接口/前端会展示），
            "icon" / "tags": 展示信息,
            "transport": streamable_http / sse,
            "url": 服务地址（为空时回退读 url_env）,
            "url_env" / "api_key_env": 环境变量名（密钥、临时地址覆盖）,
            "auth_header": 鉴权头名称（x-api-key / Authorization）,
            "timeout": 连接与调用超时（秒）,
            "enabled": 默认是否启用（进程内还可以用接口临时开关）,
            "tools": {语义名: 服务端真实工具名}（业务代码按语义名取，不写死字符串）,
            "adapter": 该 MCP 的入参适配（见下面各服务的说明）,
        },
        ...
    }
"""
import os


BUILTIN_MCP_SERVERS: dict[str, dict] = {
    # ------------------------------------------------------------------
    # OCR MCP：图片 / PDF → 文本 + 版面 + confidence（Document 子图用）
    # ------------------------------------------------------------------
    "ocr": {
        "name": "OCR MCP",
        "description": "ocr识别功能",
        "icon": "📄",
        "tags": ["内置", "文档", "OCR"],
        "transport": "streamable_http",
        # 服务地址直接写这里；留空则回退读 url_env（本地联调覆盖用）
        "url": "https://mcp.api-inference.modelscope.net/1c1ad4778a2a4d/mcp",
        "url_env": "OCR_MCP_BASE_URL",
        # 不要把密钥写进仓库：这里只写"去哪个环境变量取"
        "api_key": "",
        "api_key_env": "OCR_MCP_API_KEY",
        "auth_header": "x-api-key",
        "timeout": 30.0,
        "enabled": True,
        # 语义别名表：业务代码按左边的语义名取右边的真实工具名，没写的工具不会因此被禁用
        # （工具箱仍会列出服务端暴露的全部工具，见 tools/service.register_mcp_tools）。
        # 决定（方案 A）：业务链路**只用**一个通用 OCR 工具，所以这里只登记 recognize 一条。
        # 该 MCP 另外 7 个（银行卡 / 身份证 / 车牌 / 驾驶证 / 行驶证 / 增值税发票 / 火车票）
        # 是各自的专项能力、入参形态也不同，不接业务链路；要临时试可以走
        # POST /api/agent/toolbox/call，或加 disabled_tools 把它们挡在工具箱外。
        "tools": {"recognize": "GeneralOcrRecognition"},
        "adapter": {
            # 送文件的形态：base64 / url / path。
            # 该厂商的工具只收"图片链接"（阿里云市场证件/票据 OCR 家族），
            # 所以必须走 url 形态：附件先传到对象存储换成公网 URL（file_store.public_url）。
            "input_mode": "url",
            # 厂商入参字段名覆盖，支持占位符：
            # {file_base64} {file_path} {file_url} {mime_type} {file_name} {document_id}
            "args_template": {"pictureUrl": "{file_url}"},
        },
    },
    # ------------------------------------------------------------------
    # 财汇企业信息 MCP：企业主体信息（Application 子图用）
    #   与下面的 yjt 是**同一个 MCP 服务**（财汇 Finchina），共用一套凭据：
    #   地址读 YJT_BASE_URL、密钥读 YJT_API_KEY，不需要再单独配 COMPANY_MCP_*。
    #   财汇是"两步式"调用：导航工具列举子工具目录 → execute_tool 执行具体能力。
    #   业务代码按语义名 search 取到的真实工具名就是 execute_tool，
    #   具体调用哪个子工具由 adapter.sub_tool 指定。
    # ------------------------------------------------------------------
    "company": {
        "name": "财汇企业信息 MCP",
        "description": "按企业名称查主体信息（统一社会信用代码 / 注册地址 / 法人 / 企业性质 / 注册资本）",
        "icon": "🏢",
        "tags": ["内置", "企业", "财汇"],
        "transport": "streamable_http",
        # 完整 MCP 地址（含路径）；留空则回退读 url_env
        "url": "https://mcp.finchina.com/finchina-data-mcp-server/mcp",
        "url_env": "YJT_BASE_URL",
        "api_key": "",
        "api_key_env": "YJT_API_KEY",
        "auth_header": "x-api-key",
        "timeout": 30.0,
        "enabled": True,
        # 语义名 → 服务端真实工具名：财汇只有 execute_tool 一个执行入口
        "tools": {"search": "execute_tool"},
        "adapter": {
            # 调用约定：direct（一次调用）/ execute_tool（财汇两步式）
            "call_style": "execute_tool",
            # 子工具：企业基本指标查询（企业基础档案类目下）
            #   为什么不用 filter_companies_by_basic_info：那个是"筛选器"，
            #   只回企业名称 + 统一社会信用代码（实测），拿不到注册地址 / 法人 /
            #   注册资本 / 企业性质这些要填进申请书模板的字段；它自己的说明也写着
            #   "不支持企业工商档案、基础画像查询，建议调用企业基础档案下的子工具"。
            "sub_tool": "get_company_basic_info",
            # 子工具入参形态：target_company（按企业名查，本工具）/ parameter_list（按条件筛）
            "sub_arg_style": "target_company",
            # 固定子参数：要取哪些指标（中文指标名，支持同义词）
            "sub_args": {
                "indicator_name": [
                    "企业名称",
                    "统一社会信用代码",
                    "法定代表人",
                    "注册资本",
                    "注册资本币种",
                    "注册地址",
                    "企业性质",
                    "组织形式",
                    "所属省",
                    "所属市",
                ]
            },
            # 追加进子工具入参的额外参数，例如 {"sort_list": [...]}
            "args_template": {},
        },
    },
    # ------------------------------------------------------------------
    # 财汇资讯 MCP（原"企业预警通"）：老 RAG 链路的联网检索
    #   与上面的 company 同属财汇 MCP，共用 YJT_BASE_URL / YJT_API_KEY。
    #  注意：老链路节点 app/rag/query_process/agent/nodes/node_web_search_mcp.py
    #  仍然直接读 app/conf/mcp_config.py 的 yjt_* 字段（未改动，两处读的是同一组环境变量），
    #  这里登记它是为了让"MCP 一共几个"在一个文件里看全，也方便后续把老节点切过来。
    # ------------------------------------------------------------------
    "yjt": {
        "name": "财汇资讯 MCP",
        "description": "企业经营资讯 / 新闻检索（老 RAG 联网检索通路）",
        "icon": "📰",
        "tags": ["内置", "资讯", "老链路"],
        "transport": "streamable_http",
        "url": "",
        "url_env": "YJT_BASE_URL",
        "api_key": "",
        "api_key_env": "YJT_API_KEY",
        "auth_header": "x-api-key",
        "timeout": 30.0,
        "enabled": True,
        "tools": {"search_news": "search_news"},
        "adapter": {},
    },
}


def resolve_url(definition: dict) -> str | None:
    """解析服务地址：优先用声明里的 url，为空时回退读 url_env。"""
    url = str(definition.get("url") or "").strip()
    if url:
        return url
    env_name = str(definition.get("url_env") or "").strip()
    if not env_name:
        return None
    return (os.getenv(env_name) or "").strip() or None


def resolve_api_key(definition: dict) -> str | None:
    """解析密钥：声明里的 api_key 只做兜底，正常都从 api_key_env 指向的环境变量取。"""
    key = str(definition.get("api_key") or "").strip()
    if key:
        return key
    env_name = str(definition.get("api_key_env") or "").strip()
    if not env_name:
        return None
    return (os.getenv(env_name) or "").strip() or None
