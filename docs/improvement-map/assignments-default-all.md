# 作业管理默认全部：REQ-ASG-ALL实施规格

审计基线：`b09c39e3560cfe70b0142aab93d7b1c2411bd173`（本轮核查时的main固定提交；以下链接均固定到该SHA）。仅静态只读审查；未实现、未运行测试或真实同步/提交。

**结论：目前确实默认“今天”，并没有可用的“全部”分类。问题涉及前端、桥接和两条后端路径，不能只改一个初始字符串。**

状态：**用户明确要求，待实现。此文档交付未修改业务代码。**

[返回图谱](README.md) · [日志](2026-10-17.md) · [结构化节点](graph.json)

## 已核实事实与根因

|事实|固定SHA代码证据|
|---|---|
|无目标普通进入默认today；有目标默认upcoming。|[dashboard-web/src/components/AssignmentsView.tsx:277-283](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/components/AssignmentsView.tsx#L277-L283)|
|8个分类，没有all；today排首位。|[dashboard-web/src/components/AssignmentsView.tsx:57-66](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/components/AssignmentsView.tsx#L57-L66)|
|目标详情加载后按filters顺序找到第一个categories命中项并setCategory，可能重新写为today。|[dashboard-web/src/components/AssignmentsView.tsx:317-343](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/components/AssignmentsView.tsx#L317-L343)|
|列表请求仅携带category。|[dashboard-web/src/components/AssignmentsView.tsx:302-315](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/components/AssignmentsView.tsx#L302-L315)|
|分类按钮通过显式点击setCategory。|[dashboard-web/src/components/AssignmentsView.tsx:499-516](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/components/AssignmentsView.tsx#L499-L516)|
|当前空态为此分类暂无作业/Canvas没有返回符合条件作业。|[dashboard-web/src/components/AssignmentsView.tsx:518-526](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/components/AssignmentsView.tsx#L518-L526)|
|AssignmentCategory联合类型不含all。|[dashboard-web/src/lib/types.ts:421-429](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/lib/types.ts#L421-L429)|
|getAssignments仅传category，无课程/搜索/状态组合参数。|[dashboard-web/src/lib/api.ts:510-517](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/lib/api.ts#L510-L517)|
|桥接缺省category=today且白名单不含all，仅改前端默认值会被拒绝。|[desktop_app.py:957-963](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/desktop_app.py#L957-L963)|
|本地列表限活跃作业并按category in categories过滤，直接传all会过滤空。|[sjtu_learning_assistant/desktop_learning_service.py:190-211](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/sjtu_learning_assistant/desktop_learning_service.py#L190-L211)|
|远端路径同样按category成员过滤。|[sjtu_learning_assistant/desktop_learning_service.py:213-230](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/sjtu_learning_assistant/desktop_learning_service.py#L213-L230)|
|today按now.astimezone的本地日期判断；状态与日期同在categories中。|[sjtu_learning_assistant/desktop_learning_service.py:73-102](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/sjtu_learning_assistant/desktop_learning_service.py#L73-L102)|
|hash仅识别视图，无query/category解析；不能把目前URL筛选当已存在。|[dashboard-web/src/App.tsx:170-177](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/App.tsx#L170-L177)|
|setView只写/${next}。|[dashboard-web/src/App.tsx:1278-1281](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/App.tsx#L1278-L1281)|
|主导航作业中心仅为assignments视图。|[dashboard-web/src/components/AppShell.tsx:34-39](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/components/AppShell.tsx#L34-L39)|
|日历可带target进入作业中心。|[dashboard-web/src/App.tsx:1493-1502](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/App.tsx#L1493-L1502)|
|AssignmentsView收到initialTarget并在处理后清空。|[dashboard-web/src/App.tsx:1518-1524](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/App.tsx#L1518-L1524)|
|默认测试夹具为today；断言8分类且切换graded。|[dashboard-web/src/test/AssignmentsView.test.tsx:67-117](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/test/AssignmentsView.test.tsx#L67-L117)|
|已有日历目标详情定位回归。|[dashboard-web/src/test/AssignmentsView.test.tsx:216-232](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/test/AssignmentsView.test.tsx#L216-L232)|
|已有日历到作业工作台集成测试，mock upcoming。|[dashboard-web/src/test/App.test.tsx:774-849](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/dashboard-web/src/test/App.test.tsx#L774-L849)|
|后端测试today/missing/graded分类。|[tests/test_desktop_learning_service.py:91-98](https://github.com/Augety-silence/sjtu-learning-assistant/blob/b09c39e3560cfe70b0142aab93d7b1c2411bd173/tests/test_desktop_learning_service.py#L91-L98)|

`today` 的写入链：普通进入 → `initialTarget ? upcoming : today`；显式点击今天tab → `setCategory(today)`；定向详情加载 → `filters.find(...)` 按 today 排第一选中并写回；桥接缺省payload同样取today。没有证据表明这是URL或持久化筛选恢复导致。

## 需求边界

- **ASG-001 / P0 / user_explicit**：常规进入作业管理默认展示全部作业；不得默认只显示今天截止。 用户原意是常规进入默认全部；验收解释建议为不隐式按日期/完成状态收窄，范围沿用当前授权、已同步和有效数据边界，覆盖无DDL、已交、待批改、已评分。课程/搜索等新控件不是本轮已存在能力。
- **ASG-002 / P0 / derived_acceptance**：新增端到端all查询契约。 同步更新前端查询类型、桥接白名单/缺省、本地与远端服务路径；all绕过日期/状态分类成员过滤，保留权限和活跃数据边界，不能给每个作业伪造today/all标签。
- **ASG-003 / P1 / design_proposal**：今天截止保留为显式快捷筛选，普通导航不会隐式选中。 保留今天入口是建议，用户没有明确要求保留或移除；已有入口是分类tab，没有确认存在独立今日快捷导航。
- **ASG-004 / P1 / design_proposal**：课程、关键词、完成状态和截止范围可组合，采用AND语义并显示生效条件。 重置回全部；完成/提交状态以服务端状态为据，逾期和缺交保持独立标记。若引入已完成汇总组须另外定义状态集合，不能用已过期代替完成。
- **ASG-005 / P0 / derived_acceptance**：区分筛选空、全部空、未同步、读取失败。 筛选0项提示当前条件下没有匹配作业并提供清除筛选；全部0项说明当前已同步范围及同步状态；失败显示错误与重试，不误报今天无作业=全部无作业。
- **ASG-006 / P1 / design_proposal**：详情定位不应把常规all列表静默改为today/upcoming。 日历定向打开仍定位正确作业；可保留all上下文或显示显式来源筛选。若未来引入URL筛选，只允许明确入口携带参数，普通#/assignments仍all；当前没有URL筛选初始化。

用户明确的是“常规进入默认全部”。保留今天快捷入口、加入课程/搜索/完成状态组合、URL可分享筛选等是为解决问题提出的设计建议，不能写成用户已逐项确认。

## 验收条件（供后续 Agent 转为测试）

|编号|触发场景|必须达到的结果|层级|
|---|---|---|---|
|ASG-AC1|普通导航/直接#/assignments/离开后重新进入|默认选中全部；首次列表请求category=all；未来、逾期、无DDL、已交/已评分均可见，今天0项也不会空掉列表。|frontend+App integration|
|ASG-AC2|显式点击今天截止|只显示应用有效时区当天截止的作业；界面可见筛选条件，清除/全部可回到完整列表；测试午夜边界和无DDL。|frontend+backend timezone|
|ASG-AC3|课程A+关键词+未提交+未来日期|满足所有条件的交集；单独切换一维不清空其他维；重置恢复全部。|filter contract|
|ASG-AC4|all请求通过桥接、本地DB fake、远端Canvas fake|均通过白名单；返回完整有效数据、不因all不在categories而变空；today等旧分类仍正确。|bridge+backend isolated tests|
|ASG-AC5|日历或其他定向入口打开作业|详情定位正确且不丢失作业；不得由filters.find把普通all自动变today；保留当前详情/提交确认与服务端回读回归。|App/AssignmentsView integration|
|ASG-AC6|筛选0项/全部0项/首次未同步/API失败|四者文案和操作语义可区别；当前条件0项给清除筛选，失败给重试。|frontend state matrix|
|ASG-AC7|提交后刷新、请求乱序与同步重载|保持明确生效筛选；旧today请求不能覆盖新all结果；完成状态变化按当前条件更新；无真实账号提交。|frontend async+submission regression|

ASG-AC3属于组合筛选的扩展建议，不是最小首批修复的阻塞条件。ASG-AC2覆盖保留今天入口的建议方案。默认全部的首批必需范围为ASG-AC1/4/5/6/7。

## 实施顺序与已有回归

1. 在最新 main 的隔离工作目录补默认all、桥接all和服务all的回归；旧phase1a不能作为实现基线。
2. 明确 all 的查询语义，更新类型、桥接白名单/缺省、本地与远端过滤；保留权限、活跃数据与服务端状态判断。
3. 调整常规入口初始化和目标详情分类重写；先补差异空态；组合筛选作为独立后续增强。
4. 扩充 AssignmentsView.test.tsx（现有67-117行依赖today、8分类）、App.test.tsx（774-849行日历定位）与后端分类测试；保留服务端提交回读/确认回归。
5. 先用fake服务验证；本次需求不授权后续Agent自动执行真实提交。

未验证：真实安装版UI、当前本地同步数据完整性、真实性能。文档上传只交付规格，不等于功能已修复。
