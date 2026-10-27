# Responses API（首期兼容范围）

新增 `POST /v1/responses`、`GET /v1/responses/{id}`、`DELETE /v1/responses/{id}`。可使用标准 OpenAI Python SDK，无需额外请求头或私有参数。这里实现的是 DeepSeek 网页上游能够支持的 Responses 子集，不等同于 OpenAI 托管服务的全部功能。

## 标准 SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="你的代理 API key")
first = client.responses.create(
    model="deepseek-chat", instructions="请用中文回答。", input="记住代号 CEDAR-904。"
)
print(first.output_text)

with client.responses.stream(
    model="deepseek-chat", previous_response_id=first.id, input="我的代号是什么？"
) as stream:
    for event in stream:
        if event.type == "response.output_text.delta":
            print(event.delta, end="", flush=True)
    final = stream.get_final_response()

saved = client.responses.retrieve(final.id)
client.responses.delete(final.id)
```

`input` 支持字符串、`message` Items、`function_call`、`function_call_output`。消息支持 `system`、`developer`、`user`、`assistant`，内容支持字符串或 `input_text` / `output_text`。顶层 `instructions` 只作用于本次请求，续聊不会继承上一轮顶层指令；显式写进消息历史的 system/developer 消息仍属于历史。

## 图片

```python
import base64
from pathlib import Path

image_url = "data:image/png;base64," + base64.b64encode(Path("image.png").read_bytes()).decode()
image = client.responses.create(model="deepseek-chat", input=[{
    "role": "user", "content": [
        {"type": "input_text", "text": "请描述图片。"},
        {"type": "input_image", "image_url": image_url},
    ]
}])
followup = client.responses.create(
    model="deepseek-chat", previous_response_id=image.id, input="再描述一下图片中的颜色。"
)
```

JPEG、PNG、WebP 和公网 HTTP(S) 图片 URL 复用现有安全下载、校验、上传与处理轮询链。`function_call_output.output` 的内容数组也可携带 `input_image`。不支持 OpenAI Files `file_id`。图片数量与大小沿用 `IMAGE_*` 配置；重建后的整段 Responses 历史也受图片数量上限约束。续聊会将历史图片文件引用重新发给上游，状态中不保存原始图片或 base64。公网 URL 的 DNS/网络校验可能拒绝私网或保留地址。

## 函数调用

```python
tools = [{"type": "function", "name": "weather", "description": "查询城市天气",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                   "required": ["city"], "additionalProperties": False}, "strict": True}]
response = client.responses.create(model="deepseek-chat", input="巴黎天气如何？",
                                   tools=tools, tool_choice="required")
call = next(item for item in response.output if item.type == "function_call")
# 在客户端执行自己的函数，再传回结果；代理不会执行函数。
answer = client.responses.create(model="deepseek-chat", previous_response_id=response.id,
    input=[{"type": "function_call_output", "call_id": call.call_id,
            "output": '{"city":"Paris","temperature_c":19}'}])
```

工具定义使用 Responses 的扁平格式。`tool_choice` 支持 `auto`、`none`、`required` 或 `{"type":"function","name":"weather"}`。函数通过 DSML 提示词实现，每轮最多一次调用；`id` 是输出 Item ID，工具结果必须引用独立的 `call_id`。默认校验参数 JSON Schema，`strict=false` 仅跳过结果的 schema 校验，JSON 语法仍须有效。违反工具选择或参数约束的上游结果返回 502，流中返回 `response.failed`，不会伪装成功。远程 schema 引用不支持。

## 状态、隔离与限制

- `store` 默认 `true`，只保存成功完成的响应。`store=false` 不生成可检索状态；客户端可自行重传消息和输出 Items，图片需重传原始 URL/data URL。
- 状态只存在当前服务进程内存，重启即丢失，多 worker 不共享。调用方按 API key 哈希隔离，无鉴权时按客户端 IP 隔离。GET/DELETE 使用相同鉴权和限流。
- 每个响应是不可变快照。两个子请求可从同一 `previous_response_id` 分支，互不污染；上游为每次请求新建独立会话，并重放对应分支的完整逻辑历史。
- 续聊固定原账号与凭证指纹，避免跨账号发送文件引用。账号忙或不可用返回 503，凭证改变返回 409；请重试或重新发送完整历史和图片。
- 存储采用父指针和增量消息，按 TTL/数量/字节上限回收。祖先过期、被删除或被回收后，其子响应可能仍可检索，但不能继续重建历史；返回明确错误，需重发完整输入。
- 历史重放会增加请求文本量和上游耗时。usage 是本地文字估算，包含输出推理文字，不提供准确视觉 token 或上游账单数据。

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| `RESPONSES_TTL` | 1800 | 状态有效期（秒） |
| `RESPONSES_MAX_RECORDS` | 1000 | 全局最大记录数 |
| `RESPONSES_MAX_BYTES` | 33554432 | 已序列化状态总字节上限 |
| `RESPONSES_PER_CALLER` | 100 | 每调用方记录数上限 |
| `RESPONSES_MAX_DEPTH` | 128 | 历史祖先链深度上限 |
| `RESPONSES_MAX_HISTORY_BYTES` | 4194304 | 重建消息历史字节上限 |

流式使用具名 Responses SSE 事件，带单调递增的 `sequence_number`、稳定的 Item ID 和索引。支持 created/in_progress、输出 Item/内容段 added/delta/done、函数参数 delta/done、completed/failed。原始 DeepSeek 推理映射为 `reasoning_text`，不伪造 summary 或 encrypted_content。思考/搜索由服务端 `THINKING`、`SEARCH` 和 `MODEL_ROUTES` 策略决定。

首期明确拒绝 hosted tools、`background=true`、`conversation`、`parallel_tool_calls=true`、非空 `reasoning` 控制、加密推理、结构化输出格式、`include`、自动截断，以及显式 `max_output_tokens` / `temperature` / `top_p`。不提供 Conversations API、取消后台任务、WebSocket、音频或 PDF。代理不会默默接受这些参数并假装上游已经执行。

实现按协议转换、SSE 状态机、快照存储、HTTP 组合层分离；共享的 ChatService、账号生命周期和图片准备链不依赖 Responses。设计与执行记录见 [已确认范围](superpowers/specs/2026-10-27-responses-api.md) 和 [实现计划](superpowers/plans/2026-10-27-responses-api.md)。
