# SJTU Learning Assistant

一个面向上海交通大学学习场景的本地桌面助手，把 **Canvas 课程、交大邮箱、作业截止时间、课程资料、交大云盘和 AI 学习助手** 集中到一个应用里。

**不需要安装 Python，不需要配置数据库，也不需要打开终端。** 下载与你电脑匹配的安装包，安装后在应用“设置”中填写账号信息即可使用。

[下载最新正式版](https://github.com/Augety-silence/sjtu-learning-assistant/releases/latest)

## 主要功能

- 学习概览：集中查看近期截止事项、未读消息和课程状态。
- Canvas 同步：同步课程、公告、作业、课程资料和模块。
- 邮件聚合：在应用内查看交大邮箱邮件、正文图片和附件。
- 作业中心：按状态查看作业，并快速打开 Canvas 原页面。
- 资料归档：自动下载本学期资料，按课程和类别整理到本地。
- 云盘备份：在“云盘备份”页面手动把课程资料和邮件附件备份到交大云盘。
- AI Chat：基于本机已同步的学习数据进行问答和检索。
- 本地提醒：在新公告、新作业、新文件或临近截止时发送系统通知。

## 三步开始使用

1. 从 [Releases 页面](https://github.com/Augety-silence/sjtu-learning-assistant/releases/latest) 下载最新版。
2. 安装并打开应用。
3. 进入左侧 **“设置”**，至少配置 Canvas，然后点击右上角 **“立即同步”**。

第一次同步可能需要几分钟。同步完成后，概览、截止事项、消息和课程资料会自动出现。

## 下载哪个文件

| 你的电脑 | 推荐下载 | 说明 |
| --- | --- | --- |
| Mac（Apple 芯片） | `SJTU-Learning-Assistant-*-macOS-arm64.dmg` | 适用于 Apple Silicon Mac |
| Windows 10/11 64 位 | `SJTU-Learning-Assistant-*-Windows-x64-Setup.exe` | 推荐，大多数用户选择这个 |
| Windows 免安装使用 | `SJTU-Learning-Assistant-*-Windows-x64-portable.zip` | 解压后直接运行 |

文件名中的 `*` 是版本号。普通用户不需要下载 `.sha256` 文件。当前没有面向 Intel 芯片 Mac 的直接安装包。

## macOS 安装

1. 下载以 `macOS-arm64.dmg` 结尾的文件。
2. 双击打开 DMG。
3. 把 **SJTU Learning Assistant** 拖到 **Applications（应用程序）** 文件夹。
4. 在“应用程序”中打开 SJTU Learning Assistant。

### 如果 macOS 阻止第一次打开

当前安装包未经过 Apple 公证，首次打开可能出现安全提示。请按下面操作：

1. 在“应用程序”中找到 SJTU Learning Assistant。
2. 按住 `Control` 点击或右键点击应用，选择 **“打开”**。
3. 如果仍被阻止，进入 **系统设置 → 隐私与安全性**，点击 **“仍要打开”**。

不需要运行任何终端命令。

## Windows 安装

### 推荐：安装器

1. 下载以 `Windows-x64-Setup.exe` 结尾的文件。
2. 双击安装器，按提示完成安装。
3. 从开始菜单或桌面快捷方式打开应用。

如果 Windows SmartScreen 显示提示，可点击 **“更多信息” → “仍要运行”**。

### 免安装版

下载以 `Windows-x64-portable.zip` 结尾的文件，完整解压到固定文件夹，再双击其中的 `SJTU Learning Assistant.exe`。不要直接在压缩包里运行。

## 第一次配置

打开应用后，进入 **设置 → 连接配置**。四项连接可以分别酪�����3��7�r�������������3�"C�((����ĸ���مϾ�#�:��6C��[�#���{���Ȑ��6�f2i�����z��8XZ�Y�8K��K��Y(΋XNii�Y�j�^y�Ni�^k�8.K����hXxnZHr��6�f266W72F��V⢮�ɠ���y��[�R�K��k[~K�NZJr6�f5҆�GG3����2�6�GR�VGR�6�8 �"����XZR���Jnh�r(i"���{�⢮8 �2�h��X���[{.h��Xxny�N��nh������X��[��iky�N����z�K�Nx��8 �B�ZH�X�nyI�h�y�BF��V�8 �R�Y��X�[�NyJ����Yʂ�����{��(i"6�f2(i"K��iK��X�{�⢢K��{)��KN[�nK��Zَ8 ��F��V��	�[��X��Z��i[Ni��zK�K�j�8.K�ފhX�{��X[nK�nK�����K��K�ފh{)��KNX���ZJ�h�nXZ�[�{��^K��8 ��222"�K�NZJ~�*�z��ȎX�����Ȑ���Z�y�k�d#�c��g*8�'9��9�k��'y.+y��y�"�`��.���h����� yf��a�d�:fa9.��� �/h:g :)�ya�i!��&���H9k�9�m9�9.�9i)�`���yg,9g`;�#9/��i���[YPڝK�YK�ۘ8� ��H9c���*9.��`���yk���-�����n�oey�9k�� {�&�"�yd+��*9.����9���k���-����k�� {�#:+��/o��*9k���-����k�� x� ���g*
��+���k�8���9.�9i)�`���H8���9/빥.zacy�k���9.+yhj�a�ynm�/�ykf8� ������ˈ9.�9i)�.�y��;�"9c��`"{�"B��acy�k�d#�c�����m�od��h�-a9��yd�:`��.��fa9.��i!�.�yb,9.�9i)�.�y��8� �/h:g :)�ya�i!�
��.�9i)�.�y��\�\���[����#9nm�g*
��+���k�8���9.�9i)�.�y��8���9/빥.zacy�k���9.+y�:--9/�ykf8� �����y�"H\�\���[�9���c��.�yab:-��/���#9.#yolyd�H�[��\�� z`���yd�9�+9g,:-a9��yb�� �x� �n�9�*:+���k�hmyc��."�)乣�9/��.����:+����8�':acy�k��!�ce��'x� �������RH9�(yg���"9c��`"{�"B��aj�置后可使用 AI Chat 和 AI 资料分类。你需要准备 AI 服务提供商的 API Key，以及对应的 Base URL 和模型名称。

在 **设置 → AI 模型 → 修改配置** 中填写并保存，然后点击 **“测试连接”**。未配置 AI 时，其他功能仍可正常使用，资料整理会自动采用规则分类。

### 5. 资料归档目录

在 **设置 → 归档偏好** 中可选择资料目录。默认使用“文档”中的 `SJTU Study` 文件夹。如果不确定，保持默认设置即可。

## 完成配置后的第一次同步

1. 回到应用主界面。
2. 点击右上角 **“立即同步”**。
3. 等待右上角提示同步完成。
4. 检查 **概览、截止事项、消息、作业中心、课程资料**。

同步在后台执行，期间可以继续使用应用。首次资料较多时，请保持网络连接。

## 隐私与安全

- 学习数据默认保存在你的电脑上，不要求自行部署数据库。
- Canvas Token、邮箱密码、云盘 Token 和 AI API Key 保存在系统安全存储中：macOS 使用 Keychain，Windows 使用凭据管理器。
- 已保存的密码和 Token 不会在应用界面中回显。
- 使用 AI 功能时，请求会发送到你配置的 AI 服务；不需要 AI 时可以不配置或关闭。
- 应用不会绕过学校系统权限，能看到的内容取决于你的账号权限。

## 常见问题

### 安装后页面是空的

通常是还没有完成同步。请先配置 Canvas，再点击右上角“立即同步”。

### Canvas 显示未配置

请确认粘贴的是 Canvas Access Token，不是统一身份认证密码，并删除 Token 前后的多余空格。

### 邮箱同步失败

确认邮箱地址完整、密码可用于邮箱客户端登录。如果启用了额外安全验证，请使用客户端密码。

### AI Chat 无法使用

在“设置 → AI 模型”中检查 API Key、Base URL 和模型名称，并点击“测试连接”。Canvas 和邮件功能不依赖 AI。

### 资料保存在哪里

默认在“文档”目录下的 `SJTU Study` 文件夹，可在“设置 → 归档偏好”中更改。

### 如何更新

打开 [Releases 页面](https://github.com/Augety-silence/sjtu-learning-assistant/releases/latest) 下载最新版。macOS 用新应用替换旧应用；Windows 运行新安装器覆盖安装。正常更新不会主动删除本地数据、已归档资料或系统凭据。

## 获取帮助

请在 [GitHub Issues](https://github.com/Augety-silence/sjtu-learning-assistant/issues) 提交问题，并附上操作系统、应用版本、复现步骤和错误截图。截图前请遮住 Token、密码、API Key、邮箱地址等个人信息。

## 项目目标与发布方式

项目目标是把 Canvas 课程、交大邮箱、作业、资料归档和 AI 学习辅助集中到一个本地优先的桌面应用中，在不暴露凭据的前提下提供稳定的同步、检索、提醒和学习工作台体验。

后续功能迭代固定分为两个阶段：

1. **macOS 本机验证与原型确认**：先在 macOS 完成功能、交互和视觉验证。
2. **Windows 同步推进与跨平台确认**：原型确认后同步适配 Windows，并验证两个平台的行为和发布包。

GitHub 上的版本均按 **正式 Release** 发布并标记为 Latest，不使用 Pre-release。每个正式版本统一提供 macOS 与 Windows 安装包及 SHA-256 校验文件。

## 面向开发者

普通用户不需要阅读或执行本节内容。

- `dashboard-web/`：React + Vite 桌面界面。
- `sjtu_learning_assistant/`：Python 数据、同步、归档、通知和桌面桥接逻辑。
- `packaging/`：macOS 与 Windows 打包配置。
- `scripts/`：测试、检查和发布脚本。
- `tests/`：自动化回归测试。

参与开发前，请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md) 和 [AGENT.md](AGENT.md)。许可证及第三方依赖声明见 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
