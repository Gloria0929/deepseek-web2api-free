# Responses API 接入设计草案

日期：2026-10-27。基线：v4.0.0 / 6504f99。状态：已核对官方文档和本地实现，待确认首期范围；尚未修改产品源码。

## 目标

让标准 OpenAI SDK 的 `client.responses.create()` 在同一个 base_url/API key 下使用文字、图片、流式、函数工具及多轮输入。协议适配独立，底层复用现有 DeepSeek PoW、账号池、图片处理和 DSML 工具能力，不通过本机 HTTP 调用 chat/completions，不转换已经渲染好的 Chat Completions SSE。

## 首期建议范围

- POST /v1/responses：input 字符串/Item 数组，instructions、system/developer/user/assistant 消息，input_text/input_image。
- 非流式 Response 对象及 output Item；SDK 可正常使用 response.output_text。
- 正确的 typed SSE，包括文本、可用推理、函数调用参数、完成/失败事件；稳定 Item ID、output_index/content_index 和递增 sequence_number。
- function 工具定义、tool_choice、function_call 和 function_call_output 的 call_id 闭环。由下游执行函数，代理只生成调用并接收结果。
- previous_response_id 原生续接，包括图片引用与工具调用上下文；无需为这一模式增加 X-Conversation-Id。
- store=false 无状态完整历史重放；store=true 使用本地有界内存保存响应快照。
- GET /v1/responses/{id}、DELETE /v1/responses/{id} 对已存储响应提供基本生命周期。

首期不宣称支持 OpenAI 托管工具、WebSocket、后台任务、Conversations API、加密 reasoning、音视频、PDF、服务端结构化输出保证或并行工具执行。不支持的实际请求必须明确报错，而不是静默忽略或伪造成功。

## 现有可复用部分与需要抽取的部分

可直接复用 image_sources、image_inputs 的资产校验、DeepSeekFilesClient、prepare_turn 的图片准备思想、底层 adapter、DSML parser 和 StreamSieve。现有鉴权、限流和请求体大小限制应覆盖新入口。

当前账号获取/释放、模型能力解析、上游调用、推理/工具处理与统计仍部分混合在 server.py 的四条 handler 内。先小步抽取协议无关 ChatService 和数据类型，让现有 OpenAI/Anthropic 路径通过回归测试后，再增加 Responses 消费者。共享服务不得 import server.py 或 Responses 的 Pydantic/HTTP 模型。

## 建议模块

| 模块 | 职责 |
| --- | --- |
| chat_types.py | 规范化文字/图片/工具输入，执行选项和 TextDelta/ReasoningDelta/ToolCall/Completed 等内部事件 |
| chat_service.py | 账号租约、准备、调用、重试、统计与取消清理；输出内部事件或汇总结果 |
| responses_format.py | Responses 请求模型、Item 与内部格式转换、完整 Response 序列化；若文件过大再按模型/转换职责拆开 |
| responses_stream.py | 专属 SSE 状态机、稳定标识、索引、事件顺序与终态；汇总与非流式共用同一结果模型 |
| response_store.py | 调用方隔离、TTL/容量/字节上限、不可变响应快照、查询/删除与父链解析 |
| responses_routes.py | HTTP 路由、鉴权/限流、状态解析及调用共享服务；通过应用组合注入依赖 |

新增适配器应依赖共享服务，不依赖 chat_completions()、_handle_stream() 或已有协议响应 JSON。server.py 最终负责应用组合与保留现有公开路由，不继续堆入第三套执行路径。

## 关键语义

### 1. Responses 不是 Chat Completions 的字段别名

输入/输出以 Item 表达。文本回复是 message/output_text；函数调用是独立 function_call，而不是把 tool_calls 塞进 choices。工具结果通过 function_call_output.call_id 关联，不能混淆输出 Item 的 id 和函数 call_id。

输入图片的 input_image.image_url 映射到同一内部图片来源，沿用 JPEG/PNG/WebP、大小、像素、公网 URL 与 fake-IP 限制。input_image.file_id 仍不支持公开 Files API。

### 2. previous_response_id 必须是快照指针

不能直接把它当作 SESSION_CACHE 的可变会话键。同一个父 Response 可以产生两个后续分支，两个分支必须各自从父快照出发，不应变成按请求到达顺序续到另一分支。

每个记录保存父 ID、本轮规范化输入、输出 Items，以及账号/凭证绑定的图片引用；不保存 API key、cookies、HAR 或原始图片字节。状态存储与上游会话缓存分开，按调用方鉴权范围隔离；查询、删除及续接都校验范围。

首版优先选择从不可变快照重建逻辑历史、在同账号新上游会话执行，复用已验证可跨会话使用的图片 ID。这样可明确处理分支和当前 instructions，避免上游既有 parent 链残留先前顶层指令。待专门验证分支、指令替换语义后，才优化为直接复用上游 parent。

同账号不可用时不能把其图片 ID 交给其他账号；如果没有可迁移的原图，则明确返回需要重发完整输入的错误。文字历史在资源策略允许时可重放，但不得因此伪装图片也已恢复。

### 3. instructions 与历史分开

本轮顶层 instructions 只作用于本轮，不能自动累计到下一轮。previous_response_id 不隐含继承此前顶层 instructions；手动传入的历史 system/developer Items 则按已声明输入语义处理。需要将“本轮顶层指令”和“历史消息”区分保存。

### 4. 状态与存储边界明确

建议 store 默认为 true，以便标准 previous_response_id 调用直接工作；本地保存策略与 OpenAI 服务的保留策略不同，必须文档说明。初期采用有界内存，进程重启后状态失效，不默认落盘。提供 TTL、记录数量、总字节及父链深度限制，避免每个快照复制整段历史造成平方级内存增长。

store=false 不留下可用于查询和续接的响应记录；客户端通过重发完整 Item 历史实现多轮。对于未知、过期、已删除、其他调用方或未成功完成的父响应，返回明确错误，不能悄悄新开空上下文。

只在成功完成后提交可续接快照；取消与失败不会产生表面 completed 的可用父链。发布 response.completed 时，完整结果与可用状态已经一致。

### 5. 流式与思考

文本路径需覆盖 response.created → response.in_progress → output_item/content_part added → output_text delta/done → content_part/output_item done → response.completed。工具路径使用 function_call_arguments.delta/done；失败使用对应错误终态，不能发送假的 completed。

DeepSeek 返回的可用推理可映射为 Responses reasoning 相关 Item/事件，但不能伪造 OpenAI encrypted_content，也不能将原始推理冒充生成过的摘要。具体输出 schema 用真实 OpenAI SDK 的解析与事件聚合测试确认。

已建立的取消安全和流关闭顺序必须移到共享服务/通用流管理中，不能为 Responses 复制一套较弱的释放逻辑。

### 6. 能力诚实与兼容验证

strict 函数参数如要接受，必须执行对应 schema 校验，不能只把 strict 原样回显。strict JSON 输出、parallel_tool_calls=true、background=true、conversation、托管工具等上游没有等价保证的功能，应明确限定或拒绝。

reasoning.effort 不能伪装成具有 low/medium/high 等原生强度控制；当前 DeepSeek 只有已验证的思考开关。max_output_tokens 也不能简单沿用旧接口“忽略”的方式却声称支持，需要明确拒绝或实施真实、可测试的输出预算及 incomplete 状态。

usage 若提供则注明本地估算，包含已定义的文字/推理部分；准确视觉用量仍不可得。不把旧接口 -1 或未知值包装成精确上游计数。

## 实施顺序与验收

1. 固定现有 195 项回归基线；抽取共享服务时只移动已有行为，逐项验证四条旧路径。
2. 新增 Responses schema/归一化、非流式及专属流事件状态机；使用真实 OpenAI SDK 测试 .responses.create()、.output_text、stream=True 与 .responses.stream() 聚合行为。
3. 实现有界 ResponseStore 与 previous_response_id、store=false、查询/删除；测试同父分叉、调用方隔离、过期/删除、失败/取消不提交，以及顶层 instructions 不继承。
4. 完成文字、图片、函数调用与函数结果的闭环；测试字符串/列表输入和手动重放 output Items。
5. 用用户已授权 HAR 凭证做真实文字、图片、思考、多轮和工具验收；不保留凭证。保持旧 API 全套测试通过，再同步文档、版本及发布范围。

## 官方依据

- https://developers.openai.com/api/docs/guides/migrate-to-responses
- https://developers.openai.com/api/docs/guides/conversation-state
- https://developers.openai.com/api/reference/resources/responses/streaming-events
- https://developers.openai.com/api/docs/guides/function-calling
- https://developers.openai.com/api/docs/guides/images-vision

以上是本项目的接入建议，不代表当前 v4.0.0 已实现 Responses API，也不宣称覆盖 OpenAI 全部 Responses 能力。
