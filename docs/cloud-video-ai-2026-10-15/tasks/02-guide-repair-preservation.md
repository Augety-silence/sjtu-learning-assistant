# C2：程序约束讲义来源修复，防止改题面来通过

## 当前已做与剩余缺口
基线已将 safe_mismatch(path/expected) 传给修复请求，区分问题题面与答案来源；原 validator 仍拒绝“声称课堂原题，但题面不在 cue”。
不要重复只改提示词。剩余问题是：source-only repair 的限制尚未通过对比首回复与修复回复强制执行；模型可同时改题面、答案或主题，仍有机会通过普通 schema。

## 文件所有权
- sjtu_learning_assistant/transcript_pipeline.py
- tests/test_transcript_pipeline.py
不得改 service 或课程 Critic 的严格保留校验。

## 实施要求
对明确的 questions[i].source/课堂原题不在 cue 的来源修复，增加确定性 preservation check。
题目/主题数目与顺序、prompt、answer、type、其他主题与 evidence 均须保持。
只允许更正可由当前 cue 判定确实错误的 question.source 元数据；不得更改真实课堂原题的来源来掩盖问题。
若同一响应多道题都有同类可证明来源错误，可以收集明确有限的可修字段集合；不能为解决首个错误而放开所有 source 或其他字段。
不可解析、超限或无法证明安全的首回复应保留失败/回退。不得猜值、删题、改题面迎合原句匹配，不得扩大回复或重试预算。
合法的来源降级要通过原 validator；原错误回复、audit 严格绑定、classroom_example、cue/quote、数字保护等规则保持不变。

## 验收
合成 fixture 覆盖：
- 只改允许来源字段通过，prompt/answer 等逐项完全不变。
- 改写/删除题面、答案、主题、证据，增删/重排题目均被拒绝。
- 非失败来源字段、错误 cue 或伪造课堂题不能混入。
- 多个确证同类来源错误的有限修复集合；合法课堂原题不被降级。
- invalid JSON、截断、超限或未知上下文不越过安全回退。
- repair prompt 继续包含 path/expected，不输出 actual/原文到公开日志。

python -m pytest tests/test_transcript_pipeline.py -q
独立 commit 建议：fix(video-ai): enforce source-only guide repair preservation
不需要真实字幕或模型 key；使用 fake client 与合成课程。
