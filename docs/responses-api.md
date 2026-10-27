# Responses API 兼容范围

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

工具定义使用 Responses 的扁平格式。`tool_choice` 支持 `auto`、`none`、`required` 或 `{"type":"function","name":"weather"}`。`id` 是输出 Item ID，工具结果必须引用独立的 `call_id`。违反工具选择或参数约束的上游结果返回 502，流中返回 `response.failed`，失败不保存快照。

`strict=true` 要求支持的 schema 中所有对象关闭额外属性（`additionalProperties=false`），所有 properties 出现在 required；可选值通过显式 null 类型表达。不合格定义在请求阶段返回 400，错误带参数路径。省略或传 null 时尝试在 object/properties、items、anyOf、$defs 子集中规范化，成功后回显有效 schema 和 strict=true；合法但无法规范化的定义回退并明确回显 strict=false。规范化不会自动给字段添加 null。`strict=false` 跳过结果 schema 校验，但参数仍须有效 JSON，不允许 NaN/Infinity。提示词与 response.tools 使用同一份有效定义。

函数通过 DSML 提示词实现，严格校验发生在生成之后，不能保证网页上游每次都生成合格参数。支持可解析的本地 JSON Pointer，不支持远程引用、资源作用域 `$id`、anchors 或 dynamic refs；无效定义不会借回退绕过校验。

### 多函数调用

为保持首期兼容，本项目默认 `parallel_tool_calls=false`，每轮最多一次调用。显式设为 true 可返回最多 64 次调用，包括同一函数的多次调用；代理不执行函数。下游可乱序回传结果，但必须一次提供当前所有待解决 call_id 的结果，再请求模型继续；未知、重复或缺失结果均返回 400。

```python
response = client.responses.create(model="deepseek-chat", input="查询巴黎和伦敦天气。",
    tools=tools, tool_choice="required", parallel_tool_calls=True)
calls = [item for item in response.output if item.type == "function_call"]
results = [{"type": "function_call_output", "call_id": item.call_id,
            "output": '{"temperature_c":19}'} for item in reversed(calls)]
answer = client.responses.create(model="deepseek-chat", previous_response_id=response.id, input=results)
```

已解析的同一批调用整体校验后才发布函数 Items；批内第二项无效时，不先发送第一项成功事件。后续独立批次失败仍可能已有部分输出，但最终只能为 failed。参数 delta 可在整批 DSML 解析完成后发出，不代表上游原生逐 token 函数参数解码。

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

明确拒绝 hosted tools、`background=true`、`conversation`、非空 `reasoning` 控制、加密推理、结构化输出格式、`include`、自动截断，以及显式 `max_output_tokens` / `temperature` / `top_p`。不提供 Conversations API、取消后台任务、WebSocket、音频或 PDF。代理不会默默接受这些参数并假装上游已经执行。

## 客户端验收与诊断

独立验收使用 Python OpenAI 3.39.0 的同步/异步客户端及锁定的 JS SDK 7.45.0，连接真实回环 HTTP 服务。每种覆盖 10 组文本、流式/聚合、图片、状态/重放、工具、错误、失败终态、主动断流和超时场景。fixture 只替换上游，保留实际路由、账号池、并发槽、DSML parser 和状态链，不依赖真实凭证。

```bash
pip install -r requirements.txt -r tests/responses_compat/requirements.txt
npm ci --prefix tests/responses_compat
python scripts/run_responses_compat.py --require-all
```

此命令和 CI 必须执行全部三个客户端，缺 Node 或 SDK 即失败。普通 pytest 不要求 Node。实际上游能否遵从工具/图片请求需单独实时验收。

已准备 HAR 时可运行 `python scripts/verify_responses_live.py --har <HAR路径> --report <报告路径>`，默认验证 Python 与 JS，或用 `--client python` / `--client javascript` 单独选择。凭证仅进入临时账号库；报告只记录检查结果、异常类型/HTTP状态和匿名传输计数。验收脚本和 SDK 不额外重试；代理适配器仍按 `DEEPSEEK_RATE_LIMIT_RETRY_DELAYS` 对上游限流退避，并对空响应按现有策略重试。最终的凭证、上游或模型遵从性失败会在报告中体现。此命令会向真实上游发送测试请求，不放入 CI。

诊断使用现有 `LOG_LEVEL` / JSON logger，事件名 `responses_execution`。记录 history_replay、account_wait、session_create、image_upload、image_processing、generation、storage 耗时，以及流式 first_upstream_event/first_output_text；非流式不伪造首 token 时间。字段仅含内部请求/响应 ID、固定阶段/状态、耗时、计数和安全错误码，不含正文、图片 URL/base64、文件 ID、工具参数/结果或凭证。

观察到提交前取消时，不保存快照，并等待在途线程、关闭上游、释放账号与并发槽。提交后发生网络断开不代表客户端已收到终帧，不能承诺网络交付与快照提交原子一致。

实现按协议转换、SSE 状态机、快照存储、HTTP 组合层分离；共享的 ChatService、账号生命周期和图片准备链不依赖 Responses。设计与执行记录见 [已确认范围](superpowers/specs/2026-10-27-responses-api.md) 和 [实现计划](superpowers/plans/2026-10-27-responses-api.md)。
