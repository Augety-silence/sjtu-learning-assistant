# main 快速审阅与消融实验（2026-10-24）

本轮以抓取到的 `origin/main` 提交 `3aa54d1a76fd8603dcc43eef5629eb5ae3cc37dd`（1.8.2）为基线，在独立 worktree、分支 `codex/main-ablation-20261024` 中完成。三个较轻量子 agent 分别审阅后端查询、前端派生计算、文本分块；主 agent 复核等价性与反例。收到降低思考强度的指示后，停止扩展范围，仅完成已有修改和必要验证。

交付保留五个局部计算优化因素，另修复一处可能卡死的参数边界。生产改动未涉及依赖、锁文件、数据库迁移、版本号、发布配置或视觉样式；现按用户要求以独立 PR 交付，等待后续评审；未合并、打包或安装 App。此前 PPT/PDF/OCR、模型路由与三种视觉风格的未提交工作仍在原工作目录，本轮没有把它们混入 main 审阅。

## 实验决策

四个可运行脚本覆盖下列六个因素，并保留输出等价性检查。数字来自本轮串行复测，均为局部实验，不代表整款 App 的速度。

| 因素 | 对照与结果 | 决策 |
| --- | --- | --- |
| 课表事件去重算法 | 固定六次派生，`filter/findIndex` 对比生产 Map/Set helper。3,002 条合成输入：23,509,818 次身份比较对比 18,012 次输入访问；结果对象、顺序和首项一致 | 保留，二次扫描改为线性遍历 |
| 稳定输入下的派生复用 | 固定同一个生产 helper，六次派生对比一次派生，18,012 对比 3,002 次访问。真实组件测试覆盖输入引用不变时的重渲染、换月及桥接调用 | 保留 `useMemo`，输入引用变化时重新计算 |
| 月内/近期事件合并去重 | 独立一次加载，原 ID 去重对比 Set：3,002 条输入由 3,786,002 次比较改为 3,002 次访问。保持按 ID 首项优先，即使类型或详情不同 | 保留，共用有明确身份契约的小 helper |
| Markdown 素材扫描上限 | 10/100/1,000 个真实临时文件排序与输出一致；100,000 个惰性候选由全部枚举变为 1,001 个即拒绝，错误文字一致。该超限合成用例 Python 峰值分配 56,247,734 → 242,578 B | 保留，超限时不再完整收集和排序 |
| 超长单句切片 | 对比 main 与当前生产函数，100/10,000/1,000,000 字符输出 cue 字典完全一致。百万字符：23.183 → 17.099 ms；峰值分配 3,988,584 → 2,051,230 B；按切片长度估算复制字符 85,533,800 → 1,000,000 | 保留，避免重复复制剩余后缀 |
| 月内/近期日历请求合并 | 三种规模 × 重叠/远隔区间 × 关闭/预热 mock 缓存，12 个普通用例输出一致；重叠区间调用可由 2 次变 1 次。但相同 assignment 身份、不同日期的反例丢失近期事件 | 拒绝，生产查询代码恢复基线 |

另有一个已撤回的探索候选：缓存字幕 overlap 行长度。默认配置没有稳定收益，增加状态或分支不值得保留；`compute-results.json` 中仅保存历史观察值，不将其当作当前脚本重新测得的结果。

## 修复与代码边界

- `calendarEvents.ts` 抽出两个身份规则不同的小函数：课表按 `(id, eventType)`，月内/近期合并按 `id`。均保留首项对象及来源顺序，不引入新的请求或刷新策略。
- `KnowledgeCompilerService._source_files` 仅收集 `max_files + 1` 个候选再判断超限；有效输入仍按原规则排序并逐项执行路径、文件类型和大小检查。
- `_split_long_cue` 以索引切片替代反复复制整段剩余文本，保留内容、时间与分块顺序。
- `chunk_cues` 对 `max_chars <= 120` 立即抛出原有参数错误。基线对非空 cue、`max_chars=120` 在隔离子进程中 1 秒超时，因为实际切片长度为零；当前回归覆盖此边界及非法参数。
- `academic_features.py` 未保留日历请求合并。反例中 September 和 October 两条记录共享 assignment ID 77；先合并去重再分区会把原本存在的 October 近期事件丢掉，详见 [后端复核](backend-review.md)。

审阅也检查了作业与总览查询：作业路径存在先物化 active assignments 再分类筛选的进一步优化空间，但时间、提交状态和 raw data 参与语义，未贸然下推 SQL；总览和消息入口是固定数量的有界查询/扇出，本轮未认定为 N+1。跨模块拆分和删除代码未在这次快速审阅中展开。

## 验证记录

| 检查 | main 基线 | 当前分支 |
| --- | --- | --- |
| Python 全量（隔离应用目录） | 734 通过、1 跳过、1 个默认路径环境断言失败；302 个子测试通过 | 排除同一默认路径断言后 738 通过、1 跳过、1 deselected；307 个子测试通过 |
| 默认路径契约单测（清除路径覆盖环境变量） | 1 通过 | 1 通过 |
| 两种环境合计 | 735 通过、1 跳过 | 739 通过、1 跳过 |
| 前端全量 | 48 文件、467 测试通过 | 49 文件、470 测试通过 |
| Biome / TypeScript / Vite build | 未作为本轮基线重复测量 | 通过 |
| Python/npm 许可证、秘密模式扫描、关键模块编译、diff 空白检查 | — | 通过 |

默认路径测试断言的是未覆盖环境变量时的模块默认路径；隔离目录在 import 前通过环境变量修改默认路径，因此基线也出现同一个断言失败。单独清除 `SJTU_APP_SUPPORT_DIR`、`SJTU_DATABASE_URL`、`SJTU_ARCHIVE_ROOT` 后运行该单测，仅验证路径和被 mock 的 keyring，不打开真实数据库。本轮没有修改测试或业务逻辑来掩盖该环境差异。Python 全量另有一条既有多线程进程 fork 的 DeprecationWarning。

未连接真实 Canvas、校内 API 或 AI 模型，未使用真实课件或学习数据。本轮结果不等同于 PPT/PDF/OCR 的真实端到端验收，也不等同于桌面打包或原生交互验收。

## App 与原工作目录保护核对

原工作目录的 tracked diff 与所有未跟踪源码文件哈希，在前后核对中均一致。

**App 运行状态未能确认保持不变。** 前置快照后，安装目录主程序的修改时间变为当天 15:04，SHA-256 发生变化，原先记录的进程在末次核对时已退出。本轮及三个子 agent 未执行 App 打包、安装或进程控制；目前无法归因这一环境变化，也没有为此重启或覆盖 App。不能把本轮自动化测试通过写成“运行中的 App 未变”。前后快照保留在本机私有验收目录，未将用户路径或进程细节写入公共结果 JSON。

## 复现与原始结果

在安装项目依赖的 Python 环境与 Node 22.22.0 中，从仓库根目录执行；本轮 Python 为 3.12.15。使用临时配置目录，保持实验与个人应用数据隔离：

```sh
export SJTU_APP_SUPPORT_DIR="$(mktemp -d)"
export SJTU_ARCHIVE_ROOT="$SJTU_APP_SUPPORT_DIR/archive"
export SJTU_DATABASE_URL="sqlite:///$SJTU_APP_SUPPORT_DIR/test.sqlite3"
export PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring

node --experimental-strip-types scripts/experiments/frontend_ablation.mjs
python scripts/experiments/source_scan_ablation.py --output docs/main-audit-2026-10-24/source-scan-results.json
python scripts/experiments/compute_ablation.py
python scripts/experiments/backend_ablation.py > docs/main-audit-2026-10-24/backend-results.json

python -m pytest tests -q -k 'not test_default_is_application_support_sqlite_without_keychain_lookup'
env -u SJTU_APP_SUPPORT_DIR -u SJTU_DATABASE_URL -u SJTU_ARCHIVE_ROOT python -m pytest tests/test_database.py::DatabaseConfigTests::test_default_is_application_support_sqlite_without_keychain_lookup -q
```

前端在 `dashboard-web` 下执行 `npm run test -- --maxWorkers=2`、`npm run lint`、`npm run build`。

原始结果：[前端](frontend-results.json)、[素材扫描](source-scan-results.json)、[字幕计算](compute-results.json)、[后端](backend-results.json)、[验证摘要](validation-summary.json)。

测量限制：

- 前端计时中旧算法带比较计数器，生产 helper 不带；计时比值存在插桩偏差，因此只以输出等价和操作次数支持结论。六次重渲染是稳定输入模型，不是 React Profiler 的真实页面延迟测量。Map/Set 的等价约束是现有 string ID 类型契约，另覆盖运行时缺失 ID。
- 素材有效目录在暖文件缓存上测试，耗时有升有降（1,000 文件中位数 86.732 → 89.810 ms），不宣称正常目录加速。超限用例为惰性 Path 迭代器；上限约束消费的候选数，不保证操作系统 `rglob/scandir` 内部没有缓冲。
- 字幕计时启用 tracemalloc，三次取中位数；短样本受测量噪声影响，普通输入峰值分配没有下降，收益限定在超长单句。
- 后端是内存 mock，不含真实数据库、网络、服务器分页或重试；mock 调用次数不直接等于真实 HTTP 次数。反例优先于普通样本的平均收益。
