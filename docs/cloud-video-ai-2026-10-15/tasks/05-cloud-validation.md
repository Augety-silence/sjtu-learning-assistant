# C5：云端汇总验收与干净构建证据

依赖 C1–C4，最后独立交付验证报告。不要在云端复刻用户 Mac 上未完成的私有 PYZ 增量重封方案。

## 范围
- docs/cloud-video-ai-2026-10-15/CLOUD_DELIVERY_REPORT.md（从 template 创建）
- 仅有真实证据表明必要时，最小修改 .github/workflows/ci.yml、scripts/verify_macos_bundle.py 或新增对应测试/纯验证 helper
- 若 CI 失败来自前四任务，回到所属任务补 fix commit，不能在本项混入不相干功能或放宽检查
不修改 release.yml、版本号、权限或分支保护，不关闭安全/许可证 job。

## 标准云端环境与检查
仓库没有 pyproject.toml；依赖入口为 requirements-dev.txt/requirements.txt。
既有 CI 使用 Python3.12、Node22.22.0、npm10.9.4。

python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
npm --prefix dashboard-web ci --no-audit --no-fund
npm --prefix dashboard-web run test
npm --prefix dashboard-web run lint
npm --prefix dashboard-web run build
python scripts/check_licenses.py
node scripts/check_licenses.mjs
python scripts/scan_secrets.py
git diff --check

Node/npm 按现有 CI 精确版本与 npmjs registry 规则执行，不为本机 Node24 的临时处理改坏云端工具链。
秘密扫描遵循现有 CI；日志脱敏，不关闭扫描规则凑通过。
合成回归必须覆盖自动 prepare→worker→分段工件，以及恢复/缓存/取消/状态接口；缺失的关键跨模块回归再补，不写无意义重复测试。

## CI 与构建
既有 CI 有 Ubuntu tests/frontend/licenses/secret-scan、macos-app 与 windows-app。使用真实 Actions run URL、job conclusion、artifact URL/ID，不伪造结果。
macOS runner 可以按正常 PyInstaller clean build 产出无个人证书候选；不使用个人凭据或学校资料。构建必须归属最终提交 SHA，包内版本和前端/项目模块来源可追溯。
若构建验证尚不能证明第一方模块/资源与同次冻结源码一致，可补可移植验证而不是修改已编译 archive 去掩盖差异；结构化 code 比较需考虑 co_filename/优化级别与 marshal 的序列化差异，不能简单删断言。
不要把构建成功称为已签名、公证、安装或真实播放验收。

## 交付
报告 C1–C5 的 commit SHA、文件、实际测试与剩余限制；提交报告后，在 PR 更新最终 head SHA 和该 head 的 CI。报告提交时 CI 尚未运行，可在报告中注明待运行；最终结果和链接直接更新 PR，不必为填写 CI 链接再反复改 head 触发构建。
只有最终 head 所有必需 CI 完成才可说云端检查通过；执行条件不足时如实说明具体未验部分。
PR 保持 Draft，不 merge、不 auto-merge、不打 tag。把固定最终 SHA、报告、CI/artifact 链接交还本地验证代理。
独立 commit 建议：test(video-ai): record cloud regression and build evidence
