# SJTU Canvas Helper 开源归属与合规要求

## 源项目

- 项目：SJTU Canvas Helper
- 仓库：https://github.com/Okabe-Rintarou-0/SJTU-Canvas-Helper
- 审计 Commit：`8f57a2b1c50998b4b2b7e53168824625d7e81b78`
- 许可证：MIT License
- 版权所有：`Copyright (c) 2025 Zihong Lin`

本交付物的 `reference/SJTU-Canvas-Helper/LICENSE` 是原始许可证全文。参考代码保持原目录结构，便于追溯来源。

## 目标仓库 README 建议文本

```markdown
## 致谢与开源归属

本项目的文件预览器组织、桌面端任务进度事件和文件级 AI 交互部分，参考或改编自
[SJTU Canvas Helper](https://github.com/Okabe-Rintarou-0/SJTU-Canvas-Helper)
（审计基线：`8f57a2b1c50998b4b2b7e53168824625d7e81b78`），原项目由
Zihong Lin 以 MIT License 发布。相关许可证全文见
`third_party/SJTU-Canvas-Helper/LICENSE`，具体改编范围见 `THIRD_PARTY_NOTICES.md`。
```

只有实际使用对应能力时才写入范围；若最终只参考思想、没有复制或改编代码，应改为“设计调研参考”，避免夸大代码继承关系。

## 目标仓库 THIRD_PARTY_NOTICES 建议文本

```markdown
## SJTU Canvas Helper

- Source: https://github.com/Okabe-Rintarou-0/SJTU-Canvas-Helper
- Audited revision: `8f57a2b1c50998b4b2b7e53168824625d7e81b78`
- License: MIT
- Copyright: Copyright (c) 2025 Zihong Lin
- License text: `third_party/SJTU-Canvas-Helper/LICENSE`
- Adapted scope: [填写实际改编的文件预览、任务事件、下载状态或文件 AI 会话代码]
- Local implementation: [填写目标仓库中的对应文件]

Changes were made to adapt the original Tauri/Rust/MUI implementation to the
Python/pywebview/React architecture and the existing SJTU Learning Assistant UI.
```

## 文件级处理要求

复制或改编代码时：

1. 保留原始 `LICENSE` 全文。
2. 在目标文件附近增加简短来源注释，示例：

```ts
// Adapted from SJTU Canvas Helper at commit 8f57a2b1c50998b4b2b7e53168824625d7e81b78.
// Copyright (c) 2025 Zihong Lin; licensed under the MIT License.
```

3. 在合并前记录源文件到目标文件的映射。
4. 对大段重写仍保留归属，因为结构和表达可能构成实质性改编。
5. 新增 `react-pdf`、`pdfjs-dist`、`@cyntler/react-doc-viewer`、MUI 或其他依赖时，单独核对依赖许可证并更新锁文件和第三方声明。

## 发布检查

- `python scripts/check_licenses.py`
- `node scripts/check_licenses.mjs`
- `python scripts/scan_secrets.py`
- 检查打包后的 App 是否携带 `third_party/SJTU-Canvas-Helper/LICENSE`
- 检查 README、THIRD_PARTY_NOTICES 与实际改编范围一致
- 检查归属中的 commit 未被写成浮动分支名

## 当前参考包包含的原始文件

- 前端事件：`src/lib/events.ts`
- 预览入口：`src/components/preview_modal.tsx`
- 渲染器注册：`src/components/renderers.tsx`
- 渲染器外壳：`src/components/renderer_shell.tsx`
- PDF 预览：`src/components/pdf_renderer.tsx`
- 下载任务：`src/components/file_download_table.tsx`
- 文件 AI 会话：`src/components/file_ai_chat_modal.tsx`
- Rust 下载/Canvas 客户端：`src-tauri/src/client/basic.rs`
- Rust AI 客户端：`src-tauri/src/client/ai.rs`
- 文件解析：`src-tauri/src/client/file_parser/`
- 错误模型：`src-tauri/src/error/mod.rs`

这些文件仅构成审计参考包，不等于目标项目最终必须复制全部实现。
