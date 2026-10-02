# Prompt 03：P2 学习任务与课程上下文

请将 SJTU Learning Assistant 从“展示作业和 DDL”升级为“帮助学生明确下一步行动”的学习任务系统，并把任务、课程、资料、消息和 AI 会话连接起来。

## 前置条件

- P1 的 `LearningResource` 和 `ResourceRelation` 已稳定。
- 开始前审计实际 `Assignment`、`Submission`、日历事件、公告、邮件、Overview 和 Deadlines DTO。
- 先输出任务来源、状态所有权、迁移方式和排序规则，确认后实施。

## StudyTask 模型

建立本地学生工作流，与 Canvas 原始事实分离：

- task ID
- source type/source ID
- course ID
- title、due_at、source URL
- stage：to_understand / to_prepare / in_progress / ready_to_submit / completed
- submission status（只读同步事实）
- estimated effort、priority、risk reason
- user note、snoozed_until
- created/updated/completed time

Canvas `workflow_state`、submission state 和本地 task stage 分开保存。同步更新远端事实时不覆盖学生的本地阶段；源作业消失时标记 source inactive 并保留本地历史。

## 课程上下文

建立 `CourseContext` 聚合：

- 课程基本信息和同步健康度
- 今日/本周任务
- 最近公告与邮件
- 最近新增资料和归档状态
- 与课程关联的 AI 会话
- 课程时间线和课程级搜索

复用现有 Course、Assignment、Announcement 和 LearningResource，不复制事实数据。

## 任务生成与更新

- Canvas Assignment 自动映射到 StudyTask。
- 日历事件、公告和邮件可由学生显式转为任务。
- 截止时间变化更新远端字段，并记录 timeline event。
- 提交成功或远端确认后建议完成；本地完成状态由学生确认或明确规则推进。
- 资料缺失、未归档、临近截止、提交异常形成可解释 risk reason。
- 所有自动建议都显示依据，不把推断写成远端事实。

## API

设计并实现：

- `study_task_list(range, course, stage, risk, cursor)`
- `study_task_detail(task_id)`
- `study_task_update_stage(task_id, expected_version, stage)`
- `study_task_update_note(...)`
- `study_task_link_resource(task_id, resource_id, relation)`
- `course_context(course_id)`
- `course_timeline(course_id, cursor)`
- `today_actions()`

本地写操作使用乐观并发版本，避免多窗口或快速点击覆盖。

## 前端重构

### Overview

- 以“今天行动”“本周重点”“需要处理的异常”组织信息。
- 展示排序依据和缺失信息，不制造伪精确优先级。

### Deadlines / Tasks

- 支持时间范围、课程、阶段和风险筛选。
- 展示任务阶段、提交事实、关联资料和建议下一步。
- 支持键盘更新阶段和撤销。

### Course Context

- 聚合时间线、任务、资料、消息和 AI 入口。
- 从任务直接打开所需资料或启动带课程/任务上下文的 AI Chat。

保持现有整体布局和 AI Chat 三栏结构。

## AI 支持

- 新增任务规划上下文：任务事实、关联资料版本、截止时间和学生阶段。
- AI 可以总结要求、列准备清单、生成复习计划和解释风险。
- AI 输出建议，不直接更改任务阶段或提交状态；修改需要学生明确动作。
- Activity 展示使用的任务、课程和文件版本。

## 开源规范

P2 原则上主要使用目标项目自有模型。若继续改编参考包中的事件 Hook、文件会话 UI 或其他代码，仍执行 `OPEN_SOURCE_ATTRIBUTION.md` 的完整要求，并在来源映射中标明目标文件。

## 测试

- Assignment 到 StudyTask 的幂等映射
- 截止时间变化、源记录 inactive、本地阶段保留
- submission state 与 task stage 分离
- 乐观并发冲突和重复点击
- 时间区间、时区、无截止时间和跨学期
- 资料关系与 risk reason 可解释性
- today actions 排序稳定性
- Overview、Tasks、Course Context 的加载/空/错误/离线
- AI 上下文来源和只读事实边界
- 大量任务下的分页和响应性能

运行完整 Python 测试、前端 test/lint/build、许可证检查、秘密扫描和桌面 App smoke。

## 交付

列出模型/迁移、同步规则、任务状态机、API、前端变化、AI 上下文、测试结果、App 落地结果和下一阶段课程学习空间建议。未经允许不 commit、push 或创建 PR。
