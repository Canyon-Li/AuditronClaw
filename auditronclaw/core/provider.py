import os
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr
'''
多模型接入(Factory):所有支持项统一走 OpenAI 兼容协议。
要接入其他协议的提供商,在这里加分支并在 requirements.txt 补对应的
langchain 集成包。环境变量(OPENAI_API_KEY / OPENAI_API_BASE)由入口
加载 .env 注入,本模块只读不加载。
'''

# 各大厂商官方的 OpenAI 兼容接口地址 (当用户未配置 OPENAI_API_BASE 时作为兜底)
COMPATIBLE_BASE_URLS = {
    "aliyun": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "z.ai": "https://open.bigmodel.cn/api/paas/v4",
    "tencent": "https://api.hunyuan.cloud.tencent.com/v1"
}

# 支持的提供商名单(单一事实源:配置向导与启动自检共用)
SUPPORTED_PROVIDERS = ["openai", "aliyun", "dashscope", "z.ai", "tencent", "other"]

def get_provider(
    provider_name: str = "openai",
    model_name: str = "gpt-4o-mini"
) -> BaseChatModel:
    """
    模型适配器工厂(OpenAI 兼容协议)
    """
    provider_name = provider_name.lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        raise ValueError(f"不支持的模型提供商: {provider_name}")

    from langchain_openai import ChatOpenAI

    current_api_key = os.environ.get("OPENAI_API_KEY")
    if not current_api_key:
        raise ValueError("未找到 API Key！请确保 .env 中配置了 OPENAI_API_KEY")

    final_base_url = os.environ.get("OPENAI_API_BASE") or COMPATIBLE_BASE_URLS.get(provider_name)

    return ChatOpenAI(
        model=model_name,
        temperature=0.0,
        api_key=SecretStr(current_api_key),
        base_url=final_base_url
    )
