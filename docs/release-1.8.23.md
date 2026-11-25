# SJTU Learning Assistant 1.8.23

## 安装包发布修复

替换供应商预编译 FFmpeg，改用官方 n7.1.3 固定源码自编译的独立执行文件；禁用 GPL/nonfree/外部自动探测，保留内置解码、JPEG 截图、AAC 和音频重采样。完整对应源码、许可证、构建配方与平台证据随包发布。

冻结应用只执行自己的 ffmpeg/bin 文件，不依赖系统 PATH，也不默默退回旧供应商执行文件。macOS 与 Windows 构建均执行原生许可、配置、依赖和功能验证。

发布流程支持使用已验证的本地 Mac DMG，避免 GitHub hosted macOS runner 排队阻挡 Windows 发布；只有同版本、同提交的两套安装包和全部源码证据齐备、校验成功后才发布 Latest。未完成时保持草稿，不宣称上传完成。

## 保留行为

用户自己的 ASR Key、星辰 Ultra 默认模型、无字幕录播提取音轨和断点生成完整 MD、人工修改保护与生命周期有界重试均保留。此次构建不执行付费转写、不重编知识库。

## 草稿查询修复

GitHub 的按 tag 查询接口对草稿返回404时，使用有界认证清单精确匹配同一tag，保留真实上传者和资产元数据；不把权限或网络错误当作未发布，不重复建立草稿。已创建的1.8.21标签保持不变，本次修复使用1.8.22标签及对应证据。

## Windows 系统依赖核验

精确加入微软文档确认的 Avicap32.dll 系统组件；继续拒绝未捆绑的 libgcc/libwinpthread 等第三方 DLL，不降低依赖检查。文档：https://learn.microsoft.com/en-us/windows/win32/api/vfw/nf-vfw-capcreatecapturewindowa 。旧标签不移动，最终修复使用1.8.23。
