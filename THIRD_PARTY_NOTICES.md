# 第三方软件声明

本项目本身使用 MIT License。下表仅列入由当前锁文件/已安装包元数据确认、且会进入桌面应用运行时或前端产物的直接依赖及其运行时依赖；版本以发布构建时的锁文件和 Python 环境为准。许可证检查脚本会读取实际元数据，发现未知许可证或 GPL/AGPL 强 copyleft 运行时依赖即失败。

## Python 运行时

| 包/组件 | 许可证 | 来源 |
|---|---|---|
| SQLAlchemy、Alembic、Mako | MIT | https://github.com/sqlalchemy/sqlalchemy；https://github.com/sqlalchemy/alembic；https://github.com/sqlalchemy/mako |
| httpx、httpcore | BSD-3-Clause | https://github.com/encode/httpx；https://github.com/encode/httpcore |
| anyio、h11 | MIT | https://github.com/agronholm/anyio；https://github.com/python-hyper/h11 |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| idna、MarkupSafe | BSD-3-Clause | https://github.com/kjd/idna；https://github.com/pallets/markupsafe |
| typing_extensions | PSF-2.0 | https://github.com/python/typing_extensions |
| keyring、jaraco.classes、jaraco.context、jaraco.functools、more-itertools | MIT | https://github.com/jaraco/keyring；https://github.com/jaraco/jaraco.classes；https://github.com/jaraco/jaraco.context；https://github.com/jaraco/jaraco.functools；https://github.com/more-itertools/more-itertools |
| platformdirs | MIT | https://github.com/tox-dev/platformdirs |
| tzdata（Windows） | Apache-2.0 | https://github.com/python/tzdata |
| pywebview | BSD-3-Clause | https://github.com/r0x0r/pywebview |
| bottle、proxy_tools | MIT | https://github.com/bottlepy/bottle；https://github.com/jtushman/proxy_tools |
| PyObjC（core、Cocoa、Quartz、Security、WebKit） | MIT | https://github.com/ronaldoussoren/pyobjc |

Apple 的系统框架由 macOS 提供；Windows WebView2、.NET 运行时与系统通知框架由 Windows 提供。它们不会复制到本仓库。应用发布包应同时携带各分发包自带的完整许可证文本；本文件不是许可证全文的替代品。

## 前端运行时

| 包/组件 | 许可证 | 来源 |
|---|---|---|
| React、React DOM、scheduler | MIT | https://github.com/facebook/react |
| Radix UI primitives（`@radix-ui/*`） | MIT | https://github.com/radix-ui/primitives |
| lucide-react / Lucide icons | ISC | https://github.com/lucide-icons/lucide |
| class-variance-authority | Apache-2.0 | https://github.com/joe-bell/cva |
| clsx | MIT | https://github.com/lukeed/clsx |
| tailwind-merge | MIT | https://github.com/dcastil/tailwind-merge |
| csstype | MIT | https://github.com/frenic/csstype |

前端生产依赖的精确闭包与版本由 `dashboard-web/package-lock.json` 固定，并由 `scripts/check_licenses.mjs` 读取实际安装包的 `package.json` 验证。

## 仅开发/构建依赖（不作为默认应用运行时 API）

| 包/组件 | 许可证 | 来源/说明 |
|---|---|---|
| PyInstaller | GPL-2.0-or-later WITH Bootloader-exception | https://github.com/pyinstaller/pyinstaller；仅用于构建。其 bootloader exception 允许分发生成的应用包，不把 PyInstaller 作为本项目运行时库使用。|
| packaging | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| Pillow | MIT-CMU | https://github.com/python-pillow/Pillow；仅用于从项目原创图形生成 `.icns`。|
| Vite、Vitest、`@vitejs/plugin-react` | MIT | https://github.com/vitejs/vite；https://github.com/vitest-dev/vitest；仅构建/测试。|
| Tailwind CSS、PostCSS、Autoprefixer | MIT | https://github.com/tailwindlabs/tailwindcss；https://github.com/postcss/postcss；https://github.com/postcss/autoprefixer；仅构建。|
| Biome | MIT OR Apache-2.0 | https://github.com/biomejs/biome；仅 lint。|
| TypeScript | Apache-2.0 | https://github.com/microsoft/TypeScript；仅编译。|
| Testing Library、jsdom、类型声明及其测试工具链 | MIT | https://github.com/testing-library；https://github.com/jsdom/jsdom；https://github.com/DefinitelyTyped/DefinitelyTyped；仅测试/类型检查。|

## 可选迁移依赖

`psycopg` 与 `psycopg-binary` 使用 LGPL-3.0-only，来源为 https://github.com/psycopg/psycopg 。它们仅由 `requirements-postgres.txt` 安装，用于用户明确发起的一次性 PostgreSQL 导入、旧库检查或回滚；默认 SQLite 桌面应用不安装、不打包，也不将 PostgreSQL 驱动视为默认运行时依赖。许可证检查仅对此明确隔离的可选依赖作记录，不豁免任何其他 LGPL/GPL/AGPL 运行时依赖。

## 项目图标

`packaging/icon-source.svg` 为本项目原创的中性字母/书本图形，Copyright 2026 周济睿，按本项目 MIT License 发布；不使用上海交通大学校徽或其他学校商标。
