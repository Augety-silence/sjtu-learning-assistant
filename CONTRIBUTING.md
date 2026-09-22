# 贡献指南

感谢参与 SJTU Learning Assistant。

## 开始前

1. 阅读 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 与 [SECURITY.md](SECURITY.md)。
2. 安全问题使用 GitHub Security Advisories 私下报告，不要创建公开 Issue。
3. 不要提交个人数据、真实凭据、数据库、日志、下载资料、Keychain 导出、学校内部资料或来源不明的素材。
4. 新增依赖前确认用途、维护状态、许可证和来源；运行时不得引入 GPL/AGPL 强 copyleft 依赖。PyInstaller 仅作为带 bootloader exception 的构建工具，`psycopg` 仅用于可选 PostgreSQL 迁移/回滚。

## 本地开发

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cd dashboard-web
npm ci
npm run test
npm run lint
npm run build
cd ..
python -m unittest discover -s tests -v
python scripts/check_licenses.py
python scripts/scan_secrets.py
```

## 提交变更

- 保持改动聚焦，并为行为变化补充测试与文档。
- 前端图标统一使用 `lucide-react`，不要加入来源不明 SVG。
- 不要改变“无本地 HTTP 服务/无监听端口”的桌面安全边界。
- 数据库迁移必须说明升级、数据迁移与回滚路径；不要在测试中访问真实数据库或用户目录。
- 提交前运行上面的完整验证，并执行 `git diff --check`。
- Pull Request 中说明动机、测试结果、隐私影响、依赖/许可证变化和回滚方式。

## 代码风格

Python 遵循现有类型标注与 `unittest` 风格；前端使用 TypeScript、Biome 和现有组件约定。不要在错误信息或日志中暴露 Token、密码、数据库 URL、邮箱地址或用户绝对路径。
