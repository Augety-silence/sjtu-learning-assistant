# 知识正文读取修复与真实对话验收（2026-11-21）

## 结论与范围

用户报告 DeepSeek 综合助理仍回答“中级宏观经济学没有评分细则”。本轮修复正文读取链路，不重新编译或改写用户课件、MD、课程 Wiki、聊天数据库和凭据。正式 Wiki 仍采用概念导航；局部覆盖时由课程范围内的 MD 正文与原课件页补查，不把文件名搜索当成全文搜索。

## 开始现场与资产保护

- 正确本机仓库：`/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d`。
- 开始分支：`aime/1793618241-ai-document-multimodal`；HEAD：`40568d83bde4ddb60f7576b469b08246fd885162`；工作树干净。
- origin/main 本地引用：`d71c19bd34c4d53dba1201aa63999d77ae94a63c`。重新 fetch 遇到超时；GitHub 开放 PR 查询也因网络超时失败，不能确认最新远端冲突、依赖、CI 和审查状态。
- 沙盒是同 tree 开发副本；同步使用增量补丁，先 `git apply --check`。不覆盖用户未提交修改。
- 两个旧脏树 `/Users/bytedance/Projects/sjtu-learning-assistant`、`/Users/bytedance/Projects/sjtu-m9-nightly-20261025` 在本轮未访问、未修改。
- 未执行 reset、clean、rebase、force push、远端分支删除。

## 审计发现及证据

| 文件／位置 | 触发条件、影响与直接证据 | 修复依据 |
| --- | --- | --- |
| `agent_runtime/tools.py`，`_search_course_files` | 用户真实“中宏评分细则是咋样的 → 再查查”轨迹中，9 次工具调用只查课程、文件名、截止事项、消息；没有 MD 搜索／读取 | 文件搜索明确标注 `metadata_only/body_searched=false`，零命中不能证明正文无答案 |
| `agent_runtime/loop.py`，知识补查 | 模型有正文工具但不选择读取，软提示无法保证正文进入上下文 | 已限定 course_id 的查询自动补充授权正文检索和命中段落读取；每次真实读取记录轨迹并执行脱敏，未限定课程不自动扫全库 |
| `compiled_knowledge.py`，search | 每个文档只返回首次命中，第一章首次“评分”只是目录说明，真实评分在后部 | 保留分散的前部／后部命中及原始字符偏移；页码来自显式页面标题，不按目录叙述猜页码 |
| 同文件，多关键词 | 第一轮真实复测中模型发出“评分 考核 成绩”，原实现把整串作精确子串，错误零命中 | 最多10个空格／逗号分隔的字面关键词，任一命中；不允许正则注入、空分隔符匹配所有正文 |
| 同文件 read、tools 执行摘要 | 旧 MD 的 status=failed 容易被理解成“读取失败”；后部原页已读到仍从头逐段续读到步骤耗尽 | 分离 `read_status=ready` 与 `compilation_status`；next_offset只是分页位置；最后一轮保留给有证据、明确边界的回答 |
| `course_scope.py`，knowledge_metadata | 原课件核验需要可信文件身份，不能把 MD 路径直接交给模型打开 | 数据库关联 `course_file_source_id`；未完成旧稿的命中页可经既有受控源 ID 读取原课件 |

真实课程身份为 `course_id=94867`、`term_id=term:30`，学期 `2026-2027 Fall`。本地8份可读MD，Wiki只覆盖1/8，不能代表整门课程。第一章 MD 65,828字符，虽是 failed 旧长稿，但可读取；第26页原始提取在偏移61,442处。只读核验原 PPT 第26页，得到一致的评分构成：考勤参与5%、课后作业20%、随堂测验20%、期末55%。不采信旧自动稿中未经原页支持的加权计算推导。

## 整合顺序与修改文件

顺序：现场与失败轨迹 → MD后部/原PPT核验 → 多位置正文命中 → 限课程补查 → 真实复测暴露多词零命中 → 多词字面匹配 → 真实复测暴露重复续读 → 读取状态区分/回答预算 → 回归与全量验证 → 本地提交及精确提交打包。

业务与回归文件：

- `sjtu_learning_assistant/agent_runtime/loop.py`
- `sjtu_learning_assistant/agent_runtime/tools.py`
- `sjtu_learning_assistant/compiled_knowledge.py`
- `sjtu_learning_assistant/course_scope.py`
- `agent_presets/skills/general.md`
- `tests/test_agent_runtime.py`
- `tests/test_compiled_knowledge.py`

本记录作为第8个文件随修复提交；最终提交和部署状态在实际操作完成后追加。

## 回归与真实 API 验收

回归覆盖：文件名无评分而正文有评分；目录与后页分离；重复大量命中仍有界；多词与字面正则字符；同名课程跨学期隔离；真实页号关联；旧稿读取状态；预设权限；不扩展未指定课程；查询去重；字符、消息和时间预算；最后一轮回答；追问只继承用户问题而不继承助理错误结论。

使用本机已保存凭据，真实调用交大 `deepseek-chat`，加载正常 `general` 预设，SQLite `mode=ro` 重放用户原始3条会话消息；没有给问题额外添加“必须读取正文”的理想化验收条件。遵守服务请求限速，没有新增每日调用总量限制。未导出密钥或把凭据写入代码、日志或包。

三轮迭代记录：

1. 多关键词仍被作整体子串：零命中，无正文读取，验收失败。
2. 正文与原PPT均读取成功，但模型反复从头续读，6步耗尽：验收失败。
3. 读取状态区分后：2轮完成，9次执行，包括课程核验、文件名查询、课程范围正文搜索、4次MD读取、1次原PPT读取。实际命中MD偏移61,492、18,970、18,152、3,151，均每次1,200字符；原PPT从第26页读到第29页。回答正确列出5/20/20/55的构成，并标注第26页来源及测验排名换算、考试形式未说明的边界。原MD内容哈希前后一致，生产数据库无写入。

通过该真实场景不等于全库Wiki已编译完成，也不是Windows真机验收。

## 完整验证与部署

最终同树复核再次重放相同真实对话，仍在2轮内完成MD及第26页原课件读取，答案正确，原MD未变化。

| 检查 | Mac 本机 | 沙盒同树副本 |
| --- | --- | --- |
| 定向 Agent/MD/Wiki/历史课程 | 102 passed / 24 subtests | 101 passed / 1 skipped / 24 subtests |
| 完整 pytest | 1181 passed / 1 skipped / 484 subtests | 1179 passed / 3 skipped / 484 subtests |
| 完整 unittest | 1107 tests / OK / 1 skipped | 1107 tests / OK / 2 skipped |
| compileall、diff check | 通过 | 通过 |
| Python/npm许可证、秘密模式扫描 | 通过；7个非UTF-8文件未扫描 | 通过；7个非UTF-8文件未扫描 |
| 前端回归（前端代码未修改） | Vitest 62文件623项、Biome162文件、TypeScript/Vite通过 | 沿用前轮结果，未为本轮重复执行前端 |

Mac最终日志：`/Users/bytedance/Projects/sjtu-md-body-validation.944nUp/`，最终后端为`pytest-final.log/unittest-final.log`。沙盒最终日志：`/tmp/sjtu-md-body-final2-pytest.log`、`/tmp/sjtu-md-body-final2-unittest.log`。非阻断信息为macOS多线程fork弃用、jsdom媒体方法、Vite主chunk超过500kB；未触发GitHub CI，项目秘密扫描不等于GitHub gitleaks。

精确提交构建与部署信息将于实际完成后追加。本轮不改版本（1.8.12），不推送、创建／合并PR或发布Release。不删除已有回退。

未执行：Windows真机、真实云盘写入、全库重新编译、Mac原生逐项交互和朋友电脑复验。GitHub网络不可用，远端检查未能重新确认。

## 精确提交构建与实际部署

- 本机修复提交：`92d32a93397c6e51f4c6ea7c751812caadd3ed1a`，tree为`9b8eea06063200d2ec9859fb30e3dad66738519d`。提交前全量验证和8个文件的暂存区核验通过；提交后工作树干净。
- 沙盒修复提交：`e151579f2b1f58ae7113a3283b13a687ecd8160f`。因开发副本历史不同，哈希不同，但提交tree与本机逐字一致。
- 从本机精确修复提交的git archive创建独立构建源码：`/private/tmp/sjtu-md-body-build.H7SdJ1`。前端在独立源码重新TypeScript/Vite构建；图标与PyInstaller打包完成，没有使用脏工作树或覆盖旧构建。
- 已验签候选：`/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d/dist/md-body-reader-20261121_104450/SJTU Learning Assistant.app`。
- ad-hoc签名、deep/strict验签、plist、第一方绝对路径/秘密和禁止模块检查均通过；不是Developer ID公证。
- 已安装到：`/Applications/SJTU Learning Assistant.app`；部署后再次验签、plist检查通过；版本仍为1.8.12。
- 新主二进制SHA-256：`8d5fe3ae07e9dafdd10f544e761c0efcdb81dd76fe26c187a04970c00c68af1a`。
- 替换前旧App主PID为28547，发送正常Quit后确认主进程已退出，未SIGKILL或强制停止。部署前没有完成后台任务数据库的独立盘点；不能声称已逐项确认所有后台任务状态。
- 新时间戳回退：`/Users/bytedance/Projects/sjtu-app-rollbacks/20261121_104610-md-body-reader/SJTU Learning Assistant.app`；回退SHA-256为`69b8012c5f5b215d5241400306774ea7ba7188b809513441837cc808c7d4e04d`，与替换前一致。没有删除旧App、原有回退或学习数据，仅删除此次复制完成后的空临时安装目录。
- 部署后已启动，主进程PID33524；启动时另观察到ffmpeg子进程33543。启动和签名核验不等于Mac原生全功能交互验收。
- 真实验收脚本使用只读数据库、原MD哈希前后一致；部署启动App后正常应用生命周期可能更新本地状态，没有主动执行全库重新编译或更改历史聊天回复。

结束阶段分支仍为`aime/1793618241-ai-document-multimodal`，origin/main本地引用仍为`d71c19bd34c4d53dba1201aa63999d77ae94a63c`；修复后HEAD为上述92d32a9。随后本记录作为独立本地提交追加，结束HEAD为该部署记录提交（父提交92d32a9），外部交付消息记录最终哈希；工作树应保持干净。

本轮无push、PR创建/合并、版本变化或Release。两个旧脏工作树仍未被访问或修改。已有错误历史回复不会自动重写；新App下重新发送原问题或“再查查”以触发修复后的读取流程。
