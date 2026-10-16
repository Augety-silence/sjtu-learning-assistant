# 云端交付报告（实测填写，未执行项如实标注）

> 本文保留原云端交付时的历史状态。PR #23 已合并；2026-10-16 的后续 issue 核对、来源修复和新增回归见 [ISSUE_REGRESSION_REVIEW.md](ISSUE_REGRESSION_REVIEW.md)。最终提交、CI、构建产物及 issue 关闭记录以该后续 PR 为准。

- PR URL：https://github.com/Augety-silence/sjtu-learning-assistant/pull/23（保持 Draft，未 merge / auto-merge / undraft / tag / Release）
- 源码基线：d167f82a7e4117a4c024bf28d60f911e66c8f46c（编排文档 06f4de1）
- 测试时 SHA：c390403（C1–C4 已提交）+ 本 C5 工作区改动（tests/test_transcript_playback_bridge.py）
- 最终 head SHA：报告提交后填写到 PR（避免文件自引用 SHA）
- 平台 / Python / Node / npm：Linux x86_64 云端沙箱 / CPython 3.12.15（独立 venv，未改 HOME）/ Node v22.22.0 / npm 10.9.4（registry https://registry.npmjs.org/）
- 真实学习数据、真实模型、个人凭据：未使用（全部为合成 VTT / 假模型；未读取 Keychain、数据库、日志或用户应用目录）

| 任务 | commit SHA | 文件 | 实测命令与结果 | 未验证项 |
| --- | --- | --- | --- | --- |
| C1 #18 | 6f7d916 | sjtu_learning_assistant/transcript_service.py；tests/test_transcript_phase1_integration.py | 新增 5 个合成回归；`python -m unittest tests.test_transcript_phase1_integration tests.test_transcript_pipeline` → 82 tests OK | 真实模型下的恢复耗时与预算 |
| C2 #19 | 535e49b | sjtu_learning_assistant/transcript_pipeline.py；tests/test_transcript_pipeline.py | 新增 6 个合成回归（仅源内修复、保护词/cue/引文保持）；同上 82 tests OK | 真实模型输出下的修复命中率 |
| C3 #20 | 9917540 | sjtu_learning_assistant/transcript_service.py；dashboard-web/src/lib/types.ts；tests/test_transcript_phase1_integration.py | 新增 4 个合成回归（processing_state 优先级、processing_done / auto_retryable 一致性）；同上 82 tests OK | 旧版持久化任务在真实用户目录上的迁移表现 |
| C4 #21 | c390403 | App.tsx、TranscriptDetailDrawer.tsx、TranscriptPhase1Panels.tsx、VideoSynchronizedLearning.tsx、VideosView.tsx、lib/api.ts 及 3 个测试文件 | 新增 16 个前端用例；`npm run test` → 48 files / 456 tests passed；`npm run lint` → 121 files OK；`npm run build`（tsc -b + vite build）通过 | Mac WebView 内的真实渲染与播放器联动 |
| C5 #22 | 见 PR（本提交） | tests/test_transcript_playback_bridge.py；本报告 | 经 DesktopBridge 的 prepare→worker→分段工件回归新增 processing_state 断言（not_started→processing→ai_unconfigured），新增取消回归（cancelled、无自动恢复）；全量见下 | 见“最终 head 的 CI”与“本地仍须验证” |

## 云端本地等效检查（测试时 SHA，Linux）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Python 全量 | `python -m pip install -r requirements-dev.txt && python -m unittest discover -s tests -v`（3.12.15） | Ran 658 tests，OK（skipped=1） |
| 前端 | `npm ci --no-audit --no-fund && npm run test && npm run lint && npm run build` | 48 files / 456 tests passed；Biome 121 files 无问题；build 成功 |
| lockfile registry | 同 CI 的 grep 规则 | 无非 npmjs registry 地址 |
| 许可证 | `python scripts/check_licenses.py`；`node scripts/check_licenses.mjs` | 均通过（npm 133 个生产依赖） |
| 秘密扫描 | `python3 scripts/scan_secrets.py` | 通过 |
| 秘密扫描（gitleaks docker） | `docker run … zricethezav/gitleaks:v8.28.0 detect --redact` | 未执行：沙箱无 docker，交由 CI |
| 空白/冲突标记 | `git diff --check 06f4de1` | 通过 |
| macOS 未签名 .app | `scripts/build_macos_app.sh` | 未执行：需 macOS runner，交由 CI |
| Windows portable/安装器 | `scripts/build_windows_app.ps1` | 未执行：需 Windows runner，交由 CI |

## 最终 head 的 CI

未执行。报告提交时分项 commit 尚未推送到远端（云端环境无 GitHub 写凭据），因此没有 run URL、job conclusion 或 artifact URL/ID。推送后由 CI 对最终 head 运行，结果与链接直接更新到 PR #23，不在此处填写示例成功值。构建成功（如出现）仅表示未签名构建通过，不代表已签名、公证、安装或真实播放验收。

## 本地仍须验证

真实字幕/配置模型、个人Mac签名身份、原生视频播放与同步、正式安装、最终merge全部待本地验证代理按 LOCAL_ACCEPTANCE.md 对固定 SHA 执行。

## 依赖、隐私、兼容与回退

- 依赖：无新增或升级的 Python / npm 依赖；未修改 release.yml、版本号、权限、分支保护或 CI 安全/许可证 job。
- 隐私与权限：未新增网络监听、外部请求或权限；测试全部在临时目录使用合成数据。
- 质量门：未降低 schema、cue/引文、保护词、证据与 quality 门；原字幕与质量警告继续保留展示。
- 兼容：后端新增 `processing_state` 等字段为增量字段；前端对缺失/未知状态回退到旧字段推导，且旧响应不会被显示为“已完成”。
- 回退：按分项 commit 逆序执行 `git revert <sha>`（C5 → C4 c390403 → C2 535e49b → C3 9917540 → C1 6f7d916；C3 依赖 C1，C4 依赖 C3），不使用 reset/clean/强推。
