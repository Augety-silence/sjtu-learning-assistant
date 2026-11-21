# 课堂字幕 Markdown 与知识库落地记录

## 范围与资料契约

本轮针对“每节课只需要原始字幕、当堂老师讲了什么，并可被知识库读取”。未重新处理用户此前的成绩查询，也未改写原始课程字幕、课件和人工笔记。

- 一堂录播一份 `lesson.md`，相邻保留 `raw.vtt`、可阅读的 `original.md`、精炼的 `lecture-summary.md`。
- 总结按问题、概念关系、结论和必要条件组织；全部标题、正文和时间引用最多 2000 字符。原始字幕不受这个总结长度限制。
- 不生成题库、逐句校对稿、老师没有讲过的补充知识或自行推导的成绩计算公式。原始字幕按时间完整保留，显示为可选中文本，不执行其中的 Markdown/HTML。
- 模型选择真实字幕 ID，程序查表生成原文证据和时间标记；未知 ID、缺块、超长、截断或审校失败不发布为完成。
- 所有字幕块分批处理、逐层合并；最终稿逐块对照完整原字幕审校。结构失败只有一次有界修复，不截断总结冒充成功。
- 学期/课程使用数据库身份关联，不按标题猜学期。知识库文件夹展示为“学期/课程/课堂字幕”；磁盘以稳定录播标识存储，切换标题不生成重复身份。
- 同课程课件 ID 供回查关联，不声称自动推断出了唯一的本堂 PPT。未知对应关系保留为同课程关联。

## 审计证据与处理依据

| 文件、位置 | 触发与影响 | 处理 |
| --- | --- | --- |
| `transcript_service.py`，ARTIFACT_FILES/_organize | 已保存原始 VTT、规整字幕、JSON 小结和 V2 校对，但没有统一的课堂 MD | 新增独立课堂资料，旧材料保留；闲时逐节补齐已保存字幕，不重新跑 V1/V2 校对 |
| `compiled_knowledge.py`，_catalog | 原索引仅有课件镜像、本地项目，没有字幕来源 | 新增课堂 MD 来源；同一搜索/分页读取工具可读总结和完整原始字幕，支持课程/学期隔离 |
| `knowledge_wiki.py`，snapshot | 仅接纳完成的课件精炼稿，课堂讲解不能进入概念导航 | 完成的精炼课堂部分可参与 Wiki；引用文档仍指向含原始字幕的完整 MD，未完成/人工改稿不冒充已审校 |
| `TranscriptDetailDrawer.tsx` | 要点、练习、规整、原始、AI 校对并列且小结渲染多种训练栏目 | 主入口只保留“当堂老师讲了什么”“原始字幕”；校对与历史材料折叠，不删除 |
| `desktop_app.py`，run_desktop_app；`dashboard_service.py`，_transcript_service | 第一个打包候选启动后，已保存样本尚无 lesson.md；服务仅视频页读取时惰性启动 | 新增App启动补齐入口，仅有已保存来源时启动；使用与知识库同一用户数据根，隔离测试/自定义配置；存储故障不阻断App启动 |
| 真实交大 API，前四轮 | 模型改写了 quote、拼接了分稿栏目；过窄引用数量限制也会误拒绝有效来源 | 用真实ID本地查表；合并单独提示；保留全部有效ID，阅读正文仅显示时间范围，不放松来源检查 |

字幕记录新增字段是可选产物，不改变旧 V1/V2 完成契约，避免旧任务被误判为缺产物并重新调用模型。人工修改通过已记录 SHA-256 检测：修改或没有生成记录的同名文件保留，不覆盖。多进程补齐使用逐堂非阻塞文件锁，原有队列和取消不被长时间持锁阻塞。

已保存字幕先无 API 生成完整原文 MD，再逐堂生成精炼总结，避免首堂调用耗时挡住其他资料显示。已通过结构/来源校验的分稿检查点绑定模型、端点、原字幕和提示词；失败重试复用有效分稿，但最终逐块来源审校仍重新调用，不复用旧批准结果。

没有新增每日 API 上限，沿用交大每分钟 10 次的共享六秒限速。同一来源/模型连续失败最多自动尝试三次，每次冷却五分钟；明确重试可重置，不意味着无限重复收费请求。

## 开始现场与资产保护

- 本机：`/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d`；分支 `aime/1793618241-ai-document-multimodal`；开始 HEAD `9fec1dac54fc0e73553f65151d07847b4e0d02b3`；工作树干净。
- 沙盒同 tree 副本：`/workspace/ws1/sjtu-history-Oyj1dB`；开始 HEAD `2df538e97e14bc9f4755053efa7e2543c96ff2ec`；工作树干净。共同 tree 为 `7501f699c9de21dc6888377c5c33473e9948c484`。
- origin/main 本机引用 `d71c19bd34c4d53dba1201aa63999d77ae94a63c`。重新 fetch 超时；开放 PR、依赖、冲突、检查和审查状态不能确认最新事实。
- 两个禁止工作树未进入、未修改：`/Users/bytedance/Projects/sjtu-learning-assistant`、`/Users/bytedance/Projects/sjtu-m9-nightly-20261025`。
- 无 reset/clean/rebase/force push；只同步本轮增量补丁，应用前 `git apply --check`，保留其他修改。

## 验证

- 回归覆盖：完整原始字幕、纯文本展示、2000 字符限制、全部分块处理和审校、伪造来源拒绝、数学不等式条件、审校失败不发布、旧字幕自动补齐、课程/学期隔离、实际 MD 正文、Wiki 精炼来源、人工修改保留、失败冷却及主界面两入口。
- 启动补齐定向回归：54 passed、40 subtests passed。
- 最终沙盒 pytest：1190 passed、3 skipped、488 subtests passed（95.70 秒）；unittest：1118 tests、OK、2 skipped（90.318 秒）。
- 最终 Mac pytest：1192 passed、1 skipped、488 subtests passed（32.31 秒）；unittest：1118 tests、OK、1 skipped（29.739 秒）。平台差异来自平台专项测试/跳过项，不把数量差异当失败。
- 前端：62 文件、624 用例通过；Biome 162 文件、TypeScript 与 Vite 生产构建通过。
- compileall、Python/npm 许可证和项目秘密模式扫描通过。扫描有 7 个非 UTF-8 文件未覆盖，不等于 GitHub gitleaks。
- Mac 前端复验同样为 62 文件、624 用例通过；Biome、TypeScript/Vite、compileall、许可证与项目秘密扫描通过，无新增依赖。
- 非阻断信息：jsdom 媒体方法未实现、Vite 主 chunk 超过 500 kB、macOS 多线程 fork 弃用警告以及测试临时目录 ResourceWarning。
- 真实验收脚本 `scripts/validate_transcript_notes.py`：显式选择已保存字幕，原始文件只读、生产数据库只读、独立输出，凭据只从本机已保存位置读取，不写日志/仓库/产物。前四轮失败未发布，通过调整生成/合并契约和来源 ID 校验后，正向验收通过。

## 真实交大 API 与 AI 正文验收

- 使用本机已保存凭据、交大端点 `https://models.sjtu.edu.cn/api/v1`，真实模型 `deepseek-chat`；没有用模拟结果替代。
- 选择一份“文本分析与大模型”课堂的已保存字幕：50,258 bytes、214 条规整字幕、6 个分块。全部分块处理并独立对照原字幕审校通过，生成/合并/审校共 17 次模型调用。
- 最终“当堂老师讲了什么”为 **1065 字符**，围绕文本表示、实验比较和老师强调组织，不是逐页或逐句长讲稿。
- 综合助理通过真实 `AgentLoop/general` 对新课堂 MD 执行 **5 次正文读取**，偏移 0/4000/8000/12000/16000、每次 4000 字符；回答 1639 字符。
- `source_unchanged=true`，原字幕 SHA-256 前后一致；`production_db_writes=false`。验收输出位于独立目录 `/private/tmp/sjtu-classroom-live-20261121-114000`，没有复制回用户来源或收入代码交付。
- 人工回查了 N-gram/TF-IDF 和实验准确率相关原字幕。课堂表述与具体配置保留为老师讲授内容，不把具体实验结论冒充通用定律；单份成功不代表全部历史字幕已逐堂人工核验。

## 提交、整合顺序和结束状态

1. 沙盒功能提交 `b44b417550818fe533425df2d037c0c7a44d7534`：课堂资料、知识库与前端双入口。
2. 沙盒真实验收/续编修复 `8cab3fb6bf8890f43253f6474b1d1a9425079e26`；本机将本轮增量整合为 `6b33fecc6a5c469f3053c641969596729cb7ecab`，共同 tree `abc673ca5f7ce07d8891214ef27dd85e598917f7`。
3. 启动遗漏的修复：本机 `aedc7d874ce04ac3b75ab991381e6d60d44ddbd8`；沙盒 `18f4ccf07bee6ccef093764158a2c338ad675867`，共同 tree `93f7a7204feb0a9440e382b86ebd602a108c7fd9`。最终 App 从本机这个精确提交构建。
4. 本记录作为独立本地交付记录提交追加，不改功能代码。最终 HEAD 是该记录提交，本机/沙盒父提交分别为第3项哈希；最终消息记录本机结束 HEAD。

功能修复结束时两边分支仍为 `aime/1793618241-ai-document-multimodal`，工作树均干净。origin/main 本地引用仍为 `d71c19bd34c4d53dba1201aa63999d77ae94a63c`；远端 fetch 超时，所以不宣称引用代表最新远端，也不宣称当前 PR 检查通过。

改动文件：`transcript_notes.py`、`transcript_service.py`、`compiled_knowledge.py`、`course_scope.py`、`dashboard_service.py`、`knowledge_wiki.py`、`desktop_app.py`；前端 `TranscriptDetailDrawer.tsx`、`types.ts` 及两个相应回归文件；Python `test_transcript_notes.py`、`test_desktop_app.py`；验收脚本、本记录和产品路线。未新增依赖、数据库迁移或版本更改。

## App 精确构建、签名与回退部署

- 本轮开始安装为 1.8.12，来源为上轮提交 `92d32a93397c6e51f4c6ea7c751812caadd3ed1a`；主程序 SHA-256 `8d5fe3ae07e9dafdd10f544e761c0efcdb81dd76fe26c187a04970c00c68af1a`，开始主 PID 33524。
- 第一候选从本机 `6b33fec` 的 git archive 独立构建于 `/private/tmp/sjtu-classroom-build.5KVSta`，SHA-256 `4b0d702278509f8f841bc5bb2f54fc21d2f7f6190c08fc4761ad3ac4746f697d`。启动发现字幕服务仍惰性初始化，未将此候选当最终完成版。
- 最终从 `aedc7d874ce04ac3b75ab991381e6d60d44ddbd8` 的 git archive 创建 `/private/tmp/sjtu-classroom-build.jF3po9`，独立前端 TypeScript/Vite 构建、图标生成、PyInstaller 打包全部通过，没有覆盖工作树 build/dist。
- 使用 ad-hoc 签名，候选和安装后均通过 `codesign --verify --deep --strict`；plist、第一方个人路径/高置信秘密、禁止模块检查通过。此签名不是 Apple 公证。
- 最终安装 `/Applications/SJTU Learning Assistant.app`，版本仍 **1.8.12**；主可执行 SHA-256 **`62ae000d0b278e3109454d1311cbdb7588289d7b6d8864e6eb2fa69a859ac7aa`**。最终核验主 PID **49193**，为当时快照，不保证未来进程号不变。
- 安装前正常 Quit 并确认同可执行路径的主/子进程退出，未 SIGKILL。先创建并核对回退副本，再将旧安装另存为 `previous-installed.app` 后安装新包；没有删除任何旧回退。
- 本轮新增回退：`/Users/bytedance/Projects/sjtu-app-rollbacks/20261121_120113-classroom-notes/SJTU Learning Assistant.app`（本轮开始版）；`/Users/bytedance/Projects/sjtu-app-rollbacks/20261121_120609-classroom-startup/SJTU Learning Assistant.app`（第一候选）。相邻 `previous-installed.app` 也保留。
- 上轮 `/Users/bytedance/Projects/sjtu-app-rollbacks/20261121_104610-md-body-reader/SJTU Learning Assistant.app` 未删除。用户原始字幕、旧正式稿、人工笔记未覆盖；两个禁止旧脏树本轮未进入、未修改。

## 安装后可见性与剩余边界

- 实际安装版启动后，无需进入视频页，生成 **16 份课堂 `lesson.md`**。样本相邻 `original.md` 为完整可读原文；两者0600权限。
- 样本源 `raw.vtt` SHA-256 仍为 `2e768ed61fbc1ea3a2f66ce2ad0c037d7b70e4eeee84a0d72b164cd7dc8b5267`。生产样本当时 `lesson_notes.status=pending`，没有把未完成精炼稿显示为完成。
- 同一知识库读取服务实际目录返回16份课堂资料、0条索引问题；首份正文读取8000字符，包含“当堂老师讲了什么”和“原始字幕”。“文本”关键词命中16份；英文 `TF-IDF` 在尚未生成精炼稿的原始ASR文本中无命中，不把原始字幕术语写法当作通用同义词检索已完成。
- 主入口：知识库 → 学期 → 课程 → 课堂字幕；视频详情主阅读为“当堂老师讲了什么”“原始字幕”，旧校对/练习放入历史折叠区。存储根为用户应用数据目录 `transcripts/v1/videos/<稳定录播ID>/`，不是在源码目录硬编码真实学习资料。
- 全部精炼总结仍逐堂后台处理；真实API单份正向验收和原文可见不等于全库总结均已完成。尚未做全部历史字幕的逐堂人工复验、Windows真机、跨Mac阅读记忆或唯一PPT课时对应验证。
- 完成课堂总结可进入现有Wiki refresh的概念来源，原始正文始终可读；没有新增另一套概念生成线程，也没有声称所有概念Wiki已重新编译完。
- 本轮没有push、创建/合并PR、删除远端分支、修改版本、标签或发布Release；CI/gitleaks未重新触发。模拟回归不代替真实平台验收。
