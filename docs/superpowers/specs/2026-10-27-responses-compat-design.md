# Responses 第二阶段：客户端兼容与工具调用增强

状态：用户已批准书面规格，进入实施计划核对。基线：main / 887501ee2420874866dd230761031cc5db5a291d。

## 目标和用户意图

用户已同意推进上一轮建议的下一阶段，并允许使用 SuperPower 技能。目标是让标准客户端在真实 HTTP 环境下更可靠地使用现有 Responses 文本、图片、状态和工具流程，继续保持高内聚、低耦合。成功以客户端行为和可重复验收为依据，不以新增字段数量衡量。

本阶段交付四项：Python 同步/异步与 JavaScript SDK 兼容矩阵；请求/错误语义对齐；严格函数定义和多函数结果闭环；不记录敏感内容的阶段诊断。上一阶段 232 项回归作为基线。

## 方案比较与选择

1. 推荐：沿用现有 Responses 模块，以小模块补上工具策略、错误归类和阶段诊断；新增共享 HTTP 验收服务与三种 SDK 驱动。可分别验证，旧协议只在必要的共享生命周期和准备埋点处受影响。
2. 只增加 SDK 测试：改动较少，但不能解决 strict 默认语义、多调用批次和错误信息不足，不满足已同意的阶段目标。
3. 同时替换状态存储并重写上游执行：可扩展部署能力，但会把状态迁移和协议兼容混在一起，扩大回归面。本阶段不采用。

## 一、模块边界

- `responses_tools.py`：工具定义校验/规范化、已解析调用批次校验、待解决 call_id 集合。只依赖普通 Python 数据、jsonschema 和 Responses 错误类型，不依赖 HTTP、账号池或 SSE。
- `responses_format.py`：继续管理请求和 Items/Response 转换；调用工具策略，避免堆积 schema 实现。有效工具定义进入 prompt 与 response.tools，两者必须一致。
- `responses_errors.py`：统一安全错误分类、状态码、code、param；HTTP 和 SSE 复用同一分类结果。
- `responses_stream.py`：只管理事件序号、ID/索引、输出 Items 和终态；接收已验证调用事件，不自行决定工具策略。
- `responses_routes.py`：组合鉴权、历史、执行和存储；保留运行时依赖注入，不导入 server，不转调聊天补全 HTTP。
- `responses_observability.py`：请求级阶段计时与安全字段白名单，通过已有 logger 输出。无界历史、原始请求、凭证和业务正文均不进入日志。
- 测试支持模块负责本地服务、固定数据、SDK 驱动、端口和进程清理；不得在产品实现中加入测试专用开关。

已有 `ChatService`、`chat_runtime`、图片准备和状态快照继续复用。业务模型与 SDK 类型不得进入底层 adapter 或图片文件协议模块。

## 二、客户端兼容矩阵

Python `OpenAI`、`AsyncOpenAI`、JavaScript 官方 `openai` SDK 都连接回环地址上的真实 uvicorn HTTP 服务，不限于 TestClient/ASGI 内部调用。JS 验收使用独立 package.json 与锁文件，不加入 WebUI 的生产依赖；CI 使用与当前项目 Node 作业一致的受支持版本。

每种客户端至少验证：文本 create/output_text；原始 SSE 和 SDK 支持的聚合助手；图片及 previous_response_id 续聊；get/delete/store=false；手工重放输出 Items；多函数调用和结果关联；400/401/404/409/429/502/503；HTTP 流在首帧前/正文中/工具输出中关闭与超时。

固定服务使用合成或人工核对且脱敏的上游格式片段，覆盖文本、推理、DSML 跨片段、多个 invoke、hint/限流、半途异常和图片准备失败。测试通过正常依赖注入替换上游 transport/adapter，实际执行请求转换、HTTP、SSE 和状态路径。另保留底层真实 DSML parser/StreamSieve 回归，避免只测试人为构造的协议结果。

fixture 不包含 HAR、token、cookie、真实文件 ID、账号标识或私人对话。CI 无需 DeepSeek 或 OpenAI 托管服务凭证。测试服务器只在回环接口监听，使用测试 API key 和临时账号库，退出时按所创建的进程/句柄清理。

独立兼容测试作业必须运行三个 SDK 驱动，缺少 Node/SDK 在该作业中算失败；普通 Python 单元测试不强制安装 Node。上游实时验收为独立可选命令，使用用户已授权 HAR 的临时凭证，结果仅输出脱敏报告。

## 三、strict 与工具定义

按当前官方文档对齐以下语义，而不宣称网页上游具备受约束解码：

- `strict=true`：请求阶段检查支持的严格 schema 约束，包括所有对象 `additionalProperties=false`、所有 properties 出现在 required；可选值通过显式 null 类型表达。无效定义在获取账号之前返回 400 和具体 param。
- `strict=false`：保留普通 schema，用于提示模型，但不强制输出通过 schema 校验；参数 JSON 语法、调用名称、call_id 和工具选择约束仍必须有效。
- 省略或 `strict=null`：在可规范化的支持子集中尝试转换为严格定义，成功则在 response.tools 回显有效 schema 和 strict=true；无法转换则保持原有效 schema 并明确回显 strict=false。无效 JSON Schema、悬空/远程引用不是回退理由，必须拒绝。
- 支持子集的规范化遍历 object/properties、array/items、anyOf 和 $defs；对象关闭额外字段并将所有已定义属性设为 required，不擅自给用户字段添加 null 或改变字段值类型。对不能保持可解释语义的 schema 构造回退非严格；显式 true 使用不支持的严格构造则明确 400。
- schema 引用只支持可解析的本地 JSON Pointer；限制深度、大小，禁用网络解析。schema 校验和输出验证异常必须受控，不把嵌套类型错误变成 502。
- 参数缺失或 null 表示无参数工具，使用明确的空对象 schema；不使用真假值判断把合法 schema 悄悄替换。

有效 schema 同时用于提示词、结果校验和响应回显。严格模式是“失败即拒绝的输出验证”，不保证上游每次都生成合格参数；不增加隐式无限重试。

## 四、多函数调用与结果闭环

支持显式 `parallel_tool_calls=true` 时一轮出现多个 function_call Items。为保留首期客户端行为，本阶段默认仍为 false；这一点在兼容文档中注明。false 时返回两次及以上调用必须失败，不能截成第一次。代理不执行函数，也不声称函数在代理中并行运行。

限制每个响应最多 64 个函数调用，包括同名函数的重复调用；每次调用都有唯一 call_id 和独立 Item ID。auto 可零或多次；none 必须零次；required 至少一次；命名选择只允许该名称，在允许多调用时可多次调用同一函数。校验完整批次中的数量、名称、参数与 ID 唯一性。

将已完整解析的 DSML 调用批次作为验证单元，在发送该批次的函数 Item 之前校验全部调用，避免第一项成功发送后才发现同批第二项无效。若后续独立批次违反约束，终态为 failed；已发送片段不能撤销，不创建 completed 快照。

函数参数流仍可在 DSML 调用解析完成后发送，不伪造逐 token 参数解码。每个 Item 有各自 arguments delta/done、output_index 和稳定 ID；SDK 聚合应得到与非流式等价的调用集合。

下游可按不同顺序提交多个 function_call_output，按 call_id 关联，不能按数组位置匹配。未知或重复结果均为 400；不同输入项内重复定义 call_id 同样拒绝。要求先提供当前待解决批次的全部结果，才再次请求模型；缺失结果返回 400，不能悄悄丢弃其余调用。本阶段不增加异步工具暂存队列。

工具结果可包含图片，继续使用同账号上传和重放引用；批次及工具结果加入快照后，原有分支隔离、顶层指令替换与历史限制均生效。

## 五、请求、错误与取消语义

建立逐字段省略/null/有效值/非法值矩阵。nullable 元数据或选项按已确认官方定义处理，非 nullable 的 input/stream/store 不做不透明强制转换；未知或尚不支持字段继续明确拒绝，不为兼容而假装生效。

客户端请求错误使用 400（体积限制 413），给出安全 message、稳定 code 和 param 路径，如 tools[0].parameters 或 input[2].call_id。身份/限流/父链不可用/凭证变化/账号不可用/上游协议错误分别保留 401/429/404或409/409/503/502。不要把第三方异常字符串或请求数据原样返回。

HTTP 与流式 response.failed 共享分类；流内不能修改已发送 HTTP 状态，应通过 SDK 可解析的 error.code/message 表达失败，不把每种错误都标成同一个 server_error。终态完整且唯一，不在 failed 后继续 completed。

收到并观察到断开/取消发生在快照提交之前时，等待在途同步线程安全退出、关闭上游迭代器、释放账号和全局并发槽，不提交状态。快照在发布 completed 前提交；若远端在提交之后断开，不能承诺其一定收到了终帧，也不声称 TCP 交付和状态提交具备原子性。

## 六、阶段诊断

沿用已有结构化 logger，不增加数据库或 UI。记录 history_replay、account_wait、session_create、image_upload、image_processing、generation、storage 的耗时和结果；流式额外记录 first_upstream_event、first_output_text。非流式无法测得的首 token 不填写虚假数据。

使用单调时钟。允许字段限定为内部 request/response ID、固定阶段/终态、耗时、计数、字节数、安全错误 code。图片只记录数量/总字节，不记录 URL、base64 或原始文件 ID；不记录 prompt、输出正文、工具参数/结果、凭证、API key 或原始异常文本。预获取阶段无 response ID 时使用独立内部请求 ID。

准备链使用可选 observer/回调，旧 OpenAI/Anthropic 入口默认无操作；不为诊断复制图片上传或账号生命周期实现。诊断回调失败不得改变业务结果或泄露资源。

## 七、验收与发布边界

1. 原有 232 项回归保持通过，新增测试必须证明新语义；关键修复和功能遵守先失败后实现。
2. Python 同步、异步和 JS 在真实本地 HTTP 上通过同一份能力矩阵；断流、超时、失败及恢复检查账号和并发资源可再次获取。
3. 普通与流式多函数结果集合、ID 关联、有效工具定义保持一致；乱序回传可用，重复/缺失/未知结果明确报错。
4. 覆盖严格定义、规范化、回退、null、嵌套/本地引用、安全拒绝、批次内参数无效、旧单函数默认行为。
5. 状态、图片、工具嵌套图片、同父分支、store=false 手动重放和取消不提交继续通过；日志测试确认不输出合成敏感哨兵值。
6. 保留现有 smoke/e2e 与 Python 3.10/3.11/3.12 CI，增加可复现 SDK 验收作业和依赖锁；同步中英文兼容表与示例。
7. 用授权临时凭证做真实 Python/JS 文本/图片及多函数工具循环验收。区分协议通过与模型是否遵从；报告上游失败，不隐藏为成功。
8. 全部完成后做一次独立只读审查，修复实质问题，再全量验证。提交/推送/发版不隐含在本阶段设计审批中，遵循用户后续指令。

本阶段不做持久化/共享状态、历史压缩、输出预算、结构化文本输出保证、后台任务、Conversations、托管工具、WebSocket、PDF 或音频。内存 TTL、账号绑定、新会话重放和现有图片限制继续适用。

## 官方依据

- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)：strict 定义、默认规范化/回退、parallel_tool_calls 和 call_id。
- [Migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)：工具扁平定义、输出 Items 与工具结果关联、客户端流式更新。

以上为本项目下一阶段的实现设计；不代表当前 main 已具备新增能力，也不宣称与 OpenAI 托管执行方式等价。
