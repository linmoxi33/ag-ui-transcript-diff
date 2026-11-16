# ag-ui-transcript-diff

**把两份录制的 AG-UI 事件流各自归约成「应用真正消费到的会话」，算 sha256，再逐字段 diff——用于回答 SDK 升级后“同一个场景，客户端拿到的是不是同一个东西”。**

Upstream: https://github.com/ag-ui-protocol/ag-ui

## 上游是什么，缺什么

AG-UI 把一次 agent run 编码成事件流：`TEXT_MESSAGE_*` 拼文本、`TOOL_CALL_*` 拼工具名/参数/结果、`STATE_SNAPSHOT`/`STATE_DELTA` 维护共享状态。规范（[Runs and Steps](https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/spec/1.0/events/lifecycle.mdx)、[State](https://github.com/ag-ui-protocol/ag-ui/tree/main/docs/spec/1.0/events)）只规定事件级的合法性；[`verifyEvents`](https://github.com/ag-ui-protocol/ag-ui/tree/main/sdks/typescript/packages/client/src/verify) 校验的是事件顺序与结构。

但**事件合法 ≠ 应用看到同一结果**。上游的 conformance fixtures 断言的是单条流内的属性，没有“把两条录制归约后比较”的工具；SDK 里也没有把 chunk/delta 折叠成最终 transcript 的独立实现。跨版本、跨语言（TS/Python/.NET）迁移时，这正是最先失守又最难定位的一层。相关开放问题如 [#3368](https://github.com/ag-ui-protocol/ag-ui/issues/3368)（.NET 在 tool call 后重开文本消息，导致文本落到调用之前）就是“事件都合法、消费结果不同”的例子。

## 它做什么 / 不做什么

做：

- 解析 SSE 或 JSONL 录制；把文本增量拼成 message，把 tool args 增量拼成完整字符串，应用 JSON Patch 子集到 state。
- 输出规范化的 transcript JSON，带 `sha256`（排序键 + UTF-8，可复现）。
- `--diff` 模式逐字段报差异：D001 outcome、D010/D011 消息缺失/文本变化、D020/D021 工具缺失/字段变化、D030 state、D031 steps。
- 退出码 0（相同）/ 1（有差异）/ 2（用法或输入错误）。

不做：

- 不判事件是否合法（用同作者的 `ag-ui-stream-verify`），不联网、不起 server。
- 不做完整 RFC 6902（只支持 add/replace/remove，数组索引有限）；不执行工具，只比较参数与返回。
- 不比较 JSON 的键顺序或空白；数值 `1` 与 `1.0` 在 Python 中相等，这是已知口径。

## 安装 / 用法

零依赖，Python ≥ 3.9：

```bash
git clone https://github.com/linmoxi33/ag-ui-transcript-diff.git
cd ag-ui-transcript-diff
python3 tests/run.py                      # 10 passed
python3 agui_transcript_diff.py examples/in/a.sse --json
python3 agui_transcript_diff.py examples/in/a.sse examples/in/b.sse --json   # exit 1
```

`--json` 输出示例见 `examples/out/transcript.json` 与 `examples/out/diff.json`：

```json
{"equal": false, "left_sha256": "8afd…", "right_sha256": "c497…",
 "differences": [{"code": "D030", "path": "state", "left": {"n": 1}, "right": {"n": 2}}]}
```

## 本轮创新

B 档：把 AG-UI 兼容性判定从「单流事件合法性」推进到「**跨录制的归约结果等价性**」，并给出可复现 sha256 与字段级归因。与上游 conformance fixtures（单流断言）和 `verifyEvents`（顺序校验）判定对象不同；与同轮 `ag-ui-stream-verify` 互补（一个判合法性，一个判消费结果）。

## 局限

- state 归约只实现 JSON Patch 的常见子集，嵌套数组/复杂路径可能近似。
- 消息只按 `messageId` 归并，重复 id 的复用语义未区分。
- 未实现 `TEXT_MESSAGE_CHUNK`/`TOOL_CALL_CHUNK` 的展开（会落到未知事件被忽略）。
