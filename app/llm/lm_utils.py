# 环境配置与依赖导入
import os
from enum import Enum
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.exceptions import LangChainException
from typing import Optional

# 项目内部依赖
from app.conf.llm_config import lm_config
from app.core.logger import logger


class ModelType(Enum):
    """
    模型类型枚举，用于区分「普通文本大模型」和「视觉多模态大模型」

    LLM: 普通文本大模型（如 DeepSeek），仅支持纯文本输入
    VL:  视觉多模态大模型（如 Qwen-VL），支持图片+文本混合输入
    """
    LLM = "llm"
    VL = "vl"


# 全局缓存：键为(模型类型, 模型名, JSON输出模式)元组，值为ChatOpenAI实例
# 作用：避免重复初始化客户端，提升性能，统一实例管理
_llm_client_cache = {}


def get_llm_client(
    model_type: ModelType = ModelType.LLM,
    model: Optional[str] = None,
    json_mode: bool = False,
):
    """
    获取带全局缓存的LangChain ChatOpenAI客户端实例
    支持两种模型类型：普通文本大模型（LLM）和视觉多模态大模型（VL），自动路由到对应的API后端
    适配OpenAI/千问/DeepSeek等**OpenAI兼容API**，支持自定义模型和JSON标准化输出
    核心特性：模型类型路由 + 缓存机制 + 配置统一加载 + 异常精准捕获

    :param model_type: 模型类型，ModelType.LLM（默认，普通大模型）或 ModelType.VL（视觉大模型）
    :param model: 模型名称，优先级：传入参数 > 配置文件中的对应默认模型
    :param json_mode: 是否开启JSON输出模式，开启后返回标准json_object格式（适配结构化数据解析）
    :return: 初始化完成的ChatOpenAI实例（优先从全局缓存获取，未命中则新建并缓存）
    :raise ValueError: 缺失对应模型类型的API密钥/基础地址等核心配置
    :raise Exception: 模型初始化失败（LangChain封装层异常）
    """
    # 1. 根据模型类型选择对应的后端配置（API密钥 + BaseURL + 默认模型）
    if model_type == ModelType.VL:
        api_key = lm_config.vl_api_key
        base_url = lm_config.vl_base_url
        default_model = lm_config.vl_model
        type_label = "VL视觉模型"
    else:
        api_key = lm_config.api_key
        base_url = lm_config.base_url
        default_model = lm_config.llm_model
        type_label = "LLM文本模型"

    # 2. 确定目标模型（优先级：传入参数 > 对应类型的默认模型）
    target_model = model or default_model

    # 缓存键：模型类型 + 模型名 + JSON模式，唯一标识不同配置的客户端
    cache_key = (model_type, target_model, json_mode)

    # 3. 缓存命中：直接返回已初始化的实例，避免重复创建
    if cache_key in _llm_client_cache:
        logger.debug(
            f"[LLM客户端] 缓存命中，直接返回实例："
            f"类型={type_label}，模型={target_model}，JSON模式={json_mode}"
        )
        return _llm_client_cache[cache_key]

    # 4. 核心配置校验：拦截缺失的API关键配置，提前抛出明确异常
    if not api_key:
        raise ValueError(f"[LLM客户端] 配置缺失：请在.env中配置{type_label}的API密钥")
    if not base_url:
        raise ValueError(f"[LLM客户端] 配置缺失：请在.env中配置{type_label}的API接口基础地址")

    logger.info(
        f"[LLM客户端] 开始初始化新实例："
        f"类型={type_label}，模型={target_model}，JSON模式={json_mode}"
    )

    # 5. 配置参数组装：区分「模型私有参数」和「OpenAI通用参数」
    # extra_body：模型专属私有参数（LangChain透传至API）
    extra_body = {}
    if model_type == ModelType.VL:
        # VL模型（千问等）专属：关闭思考链输出，减少冗余内容
        extra_body["enable_thinking"] = False

    # model_kwargs：OpenAI通用参数，所有兼容API均支持
    model_kwargs = {}
    if json_mode:
        # 开启JSON标准输出模式，强制模型返回可解析的json_object
        model_kwargs["response_format"] = {"type": "json_object"}
        logger.debug("[LLM客户端] 已开启JSON输出模式，模型将返回标准JSON结构")

    # 6. 客户端初始化：捕获LangChain封装层异常，抛出更友好的提示
    try:
        llm_client = init_chat_model(
            model=target_model,
            model_provider="openai",
            api_key=api_key,
            base_url=base_url,
            extra_body=extra_body,
            model_kwargs=model_kwargs,
        )
    except LangChainException as e:
        raise Exception(
            f"[LLM客户端] {type_label}模型【{target_model}】初始化失败（LangChain层）：{str(e)}"
        ) from e

    # 7. 新实例存入全局缓存，供后续调用复用
    _llm_client_cache[cache_key] = llm_client
    logger.info(
        f"[LLM客户端] 实例初始化成功并缓存："
        f"类型={type_label}，模型={target_model}，JSON模式={json_mode}"
    )

    return llm_client


# 测试示例：验证客户端创建、缓存机制及日志输出
if __name__ == "__main__":
    logger.info("===== 开始执行LLM客户端工具测试 =====")
    try:
        # 测试1：默认普通文本大模型（LLM）
        client1 = get_llm_client()
        logger.info("✅ 测试1通过：默认LLM文本模型客户端创建成功")

        # 测试2：指定视觉多模态模型（VL）
        client2 = get_llm_client(ModelType.VL)
        logger.info("✅ 测试2通过：VL视觉模型客户端创建成功")

        # 测试3：同一类型+同一模型，验证缓存命中
        client3 = get_llm_client(ModelType.VL)
        logger.info(f"✅ 测试3通过：缓存机制验证成功，client2与client3为同一实例：{client2 is client3}")

        # 测试4：LLM文本模型 + JSON输出模式
        client4 = get_llm_client(ModelType.LLM, json_mode=True)
        logger.info("✅ 测试4通过：LLM文本模型JSON输出模式客户端创建成功")

        # 测试5：不同类型客户端相互独立（LLM 与 VL 缓存隔离）
        client5 = get_llm_client(ModelType.LLM)
        logger.info(f"✅ 测试5通过：不同类型缓存隔离验证，client1与client5为同一实例：{client1 is client5}")
        logger.info(f"   client1(LLM)与client3(VL)为不同实例：{client1 is not client3}")

    except Exception as e:
        logger.error(f"❌ LLM客户端工具测试失败：{str(e)}", exc_info=True)
    finally:
        logger.info("===== LLM客户端工具测试结束 =====")