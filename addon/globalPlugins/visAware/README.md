# Vis Aware 的 Kimi 集成

本目录中的 Kimi 实现覆盖图像描述、追问、OCR 和桌面 Agent。引擎复用 Vis Aware
现有的自动发现、设置、网络错误、识别历史、流式结果和 Agent action schema，不需要单独注册。

## API 与认证

所有 Kimi 功能统一使用 OpenAI-compatible Chat Completions：

- 请求端点：`POST /v1/chat/completions`
- 认证头：`Authorization: Bearer <API key>`
- 图像输入：`messages[].content` 数组中的 `image_url` data URL
- 输出上限：使用 `max_completion_tokens`，不使用已弃用的 `max_tokens`

默认 Base URL 为 Kimi Code 官方给出的 `https://api.kimi.com/coding/v1`，URL helper 会补全
`/chat/completions`。公共 Kimi API 可配置为 `https://api.moonshot.ai/v1`。helper 也兼容未包含
`/v1` 或已经包含 `/chat/completions` 的地址。

## 模型与思考

| 模型系列 | 预设模型 ID | 请求设置 | 多轮要求 |
| --- | --- | --- | --- |
| K3 | `k3`、`k3-256k`、`kimi-k3` | 顶层 `reasoning_effort`: `low` / `high` / `max` | 原样保留完整 assistant message；`reasoning_content` 若返回则必须保留 |
| K2.7 Code | `kimi-for-coding`、`kimi-for-coding-highspeed`、`kimi-k2.7-code`、`kimi-k2.7-code-highspeed` | 不发送 `thinking` 或 `reasoning_effort` | 思考和 Preserved Thinking 始终开启，必须保留每轮 `reasoning_content` |
| K2.6 | `kimi-k2.6` | `thinking.type` 为 `enabled` / `disabled`；启用时同时发送 `keep: "all"` | 启用思考时必须保留每轮 `reasoning_content` |

K3 始终思考，不能关闭。Vis Aware 默认使用 `high`；Kimi Code 文档也将 K3 的默认 effort
标为 `high`。K3 或 K2.7 不会收到它们不支持的关闭思考参数。

模型预设按官方端点分开：Kimi Code 只显示 `k3`、`k3-256k`、`kimi-for-coding` 和
`kimi-for-coding-highspeed`；公共 API 只显示 `kimi-k3`、`kimi-k2.7-code`、
`kimi-k2.7-code-highspeed` 和 `kimi-k2.6`。

## 图像描述与追问

图像和文字作为同一个 user message 的两个 content parts 发送，不能把 content 数组序列化为
字符串。非流式响应检查唯一 choice、assistant role 和 `finish_reason`；流式响应按 OpenAI SSE
重建 `content`、`reasoning_content`、usage 和结束原因，并要求收到 `[DONE]`。

追问会重新发送原图和初始描述，并按模型上下文、API usage 和预留输出空间保留最新的完整问答
轮次。对支持 Preserved Thinking 的模型，assistant message 会原样保存和回放，而不是只保存用户可见文字。K3 合法响应可以不包含
`reasoning_content`；K2.7 和启用思考的 K2.6 缺失该字段时拒绝继续，避免提交不完整历史。

## OCR

K3 和 K2.7 使用 `response_format: {"type": "json_schema"}` 与 `strict: true`。K2.6
使用 `json_object`，因为官方文档说明 K2.6 对复杂 schema 的行为较不稳定。所有模型的结果都会
再次进行本地校验，包括 JSON 完整性、字段类型、每个 box 恰好四个有限数值以及坐标换算。

OCR 坐标格式为 `[ymin, xmin, ymax, xmax]`，范围按 0-1000 归一化。越界坐标会裁剪，反向坐标
会交换，退化 box 会忽略。`finish_reason: length` 永远视为截断错误。

## Agent

Agent 使用标准 function tool loop，并在每轮保存完整 assistant message、`reasoning_content`、
`tool_calls`，然后以匹配的 `tool_call_id` 返回 tool message。

- K3 按官方 Agent 示例使用非流式 Chat Completions，并发送 `tool_choice: "required"`。不要发送
  指定函数对象形式的 `tool_choice`：官方文档明确说明该形式与思考模式不兼容。
- K2.7 以及启用思考的 K2.6 使用官方建议的流式工具调用，且不发送它们不支持的
  `tool_choice: "required"`。
- Agent 只接受一个名为 `agent_decision` 的完整 tool call，并要求
  `finish_reason: "tool_calls"`。缺失或重复 call ID、非法 arguments、截断和不完整 SSE 都会失败。
- 历史按完整工具轮次裁剪，只保留最新截图，并在发送前检查请求体大小。

K2.7/K2.6 思考工具调用的 `max_completion_tokens` 为 16384，满足官方要求的至少 16000。

## 日志与错误

Kimi API key、Authorization header 和图像 data URL 会在 verbose 日志中脱敏。HTTP 错误沿用
共享网络层；Kimi JSON/SSE 业务错误、未知结束原因、缺失结束标记和截断会转换为 Vis Aware
现有的 `ApiError` 或 `StreamIncompleteError`。

## 官方文档

- [Kimi Code models](https://www.kimi.com/code/docs/en/kimi-code/models.html)
- [Create Chat Completion](https://platform.kimi.ai/docs/api/chat.md)
- [Kimi model parameter reference](https://platform.kimi.ai/docs/api/models-overview.md)
- [Build an Agent with Kimi K3](https://platform.kimi.ai/docs/guide/use-kimi-k3-to-setup-agent.md)
- [Kimi K3 tool-calling best practices](https://platform.kimi.ai/docs/guide/kimi-k3-tool-calling-best-practice.md)
- [Tool Choice](https://platform.kimi.ai/docs/guide/use-tool-choice.md)
- [Thinking models](https://platform.kimi.ai/docs/guide/use-thinking-models.md)
- [Structured Output](https://platform.kimi.ai/docs/guide/response_format.md)
- [Vision model](https://platform.kimi.ai/docs/guide/use-kimi-vision-model.md)
