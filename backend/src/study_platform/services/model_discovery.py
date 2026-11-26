"""Public connection presets and bounded OpenAI-compatible model discovery."""

import asyncio
import json
from typing import Literal

import httpx
from pydantic import BaseModel

from study_platform.core.errors import ApiError
from study_platform.services.model_network import async_client, normalize_url

ModelKind = Literal["llm", "embedding", "vision", "rerank"]


class ModelProvider(BaseModel):
    id: str
    name: str
    base_url: str
    models: dict[ModelKind, str | None]
    discovery: bool = False
    rerank_path: str | None = None
    key_label: str = "API Key"
    hint: str | None = None


PROVIDERS = (
    ModelProvider(id="deepseek", name="DeepSeek", base_url="https://api.deepseek.com",
                  models={"llm": "deepseek-flash"}, discovery=True),
    ModelProvider(id="dashscope", name="阿里云百炼 DashScope", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                  models={"llm": "qwen3.5-plus", "embedding": "qwen3.7-text-embedding", "vision": "qwen3.5-ocr",
                          "rerank": "qwen3.7-text-rerank"}, rerank_path="/compatible-api/v1/reranks",
                  hint="默认是北京公共地址，请使用与 Key 匹配的区域或工作空间；重排使用同一域名的兼容接口。"),
    ModelProvider(id="siliconflow", name="硅基流动", base_url="https://api.siliconflow.cn/v1",
                  models={"llm": "deepseek-ai/DeepSeek-V3", "embedding": "BAAI/bge-m3", "vision": "Qwen/Qwen2.5-VL-72B-Instruct",
                          "rerank": "BAAI/bge-reranker-v2-m3"}, discovery=True, rerank_path="/v1/rerank"),
    ModelProvider(id="bigmodel", name="智谱 BigModel", base_url="https://open.bigmodel.cn/api/paas/v4",
                  models={"llm": "glm-4.5", "embedding": "embedding-3", "vision": "glm-4.5v"}),
    ModelProvider(id="ark", name="火山方舟（豆包）", base_url="https://ark.cn-beijing.volces.com/api/v3",
                  models={"llm": None, "embedding": None, "vision": None},
                  hint="请为每种用途填写你在方舟创建的推理接入点 ID，例如 ep- 开头的 ID。"),
    ModelProvider(id="moonshot", name="月之暗面 Kimi", base_url="https://api.moonshot.cn/v1",
                  models={"llm": None, "vision": None}, discovery=True),
    ModelProvider(id="hunyuan", name="腾讯混元", base_url="https://api.hunyuan.cloud.tencent.com/v1",
                  models={"llm": "hunyuan-turbos-latest", "embedding": "hunyuan-embedding", "vision": "hunyuan-vision"},
                  hint="这是混元旧平台的地址。已迁移到 TokenHub 的账号请使用自定义地址和新 Key。"),
    ModelProvider(id="qianfan", name="百度千帆", base_url="https://qianfan.baidubce.com/v2",
                  models={"llm": None, "embedding": "bge-large-zh", "rerank": "bce-reranker-base"},
                  discovery=True, rerank_path="/v2/rerank"),
    ModelProvider(id="minimax", name="MiniMax", base_url="https://api.minimaxi.com/v1",
                  models={"llm": "MiniMax-M3.1-Flash"}),
    ModelProvider(id="stepfun", name="阶跃星辰", base_url="https://api.stepfun.com/v1",
                  models={"llm": "step-3.7-flash", "vision": "step-1o-turbo-vision"}),
    ModelProvider(id="spark", name="讯飞星火", base_url="https://spark-api-open.xf-yun.com/v1",
                  models={"llm": "4.0Ultra"}, key_label="HTTP APIPassword"),
)


class DiscoveredModel(BaseModel):
    id: str
    name: str | None = None


def parse_models(raw: bytes | bytearray) -> list[DiscoveredModel]:
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ApiError(502, "model_discovery_invalid", "服务返回的模型列表无法读取，请手填模型 ID。")
    models: dict[str, DiscoveredModel] = {}
    for item in payload["data"]:
        model = item.get("id") if isinstance(item, dict) else None
        if not isinstance(model, str) or not 1 <= len(model) <= 120 or not model.strip() or not model.isprintable():
            continue
        label = item.get("name") or item.get("display_name")
        name = label[:120] if isinstance(label, str) and label.isprintable() else None
        models.setdefault(model, DiscoveredModel(id=model, name=name))
        if len(models) == 200:
            break
    return list(models.values())


async def discover_models(base_url: str, key: str) -> list[DiscoveredModel]:
    base_url = normalize_url(base_url)
    if not key or any(not 33 <= ord(char) <= 126 for char in key):
        raise ApiError(422, "model_key_required", "请填写有效的模型 API Key。")
    data = bytearray()
    try:
        async with asyncio.timeout(10), async_client(base_url, 8) as client, client.stream(
            "GET", f"{base_url}/models",
            headers={"Authorization": f"Bearer {key}", "Accept-Encoding": "identity"},
        ) as response:
            if response.status_code in (404, 405, 501):
                raise ApiError(422, "model_discovery_unsupported", "此地址未提供模型列表，请手填模型 ID。")
            if response.status_code in (401, 403):
                raise ApiError(502, "model_auth_failed", "模型列表鉴权失败，请核对 Key 和 API 地址。")
            if response.status_code == 429:
                raise ApiError(429, "model_quota_unavailable", "模型服务限制查询频率，请稍后重试或手填。")
            response.raise_for_status()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ApiError(502, "model_discovery_invalid", "模型列表编码不受支持，请手填模型 ID。")
            async for chunk in response.aiter_raw():
                if len(data) + len(chunk) > 512 * 1024:
                    raise ApiError(502, "model_discovery_invalid", "模型列表过大，请手填模型 ID。")
                data.extend(chunk)
    except (TimeoutError, httpx.TimeoutException):
        raise ApiError(504, "model_timeout", "读取模型列表超时，请重试或手填。", retryable=True) from None
    except httpx.HTTPError:
        raise ApiError(502, "model_service_error", "暂时无法读取模型列表，请核对地址或手填。") from None
    return [model for model in parse_models(data) if key not in model.id and key not in (model.name or "")]
