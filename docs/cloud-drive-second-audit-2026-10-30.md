# 云盘重构第二轮独立审计（2026-10-30）

范围为 `origin/main@f4ff8fd...c46bef9` 及为修复直接相关问题而调整的交大云盘 provider。起始工作树干净；起始状态、日志、统计和完整差异保存在 `/private/tmp/sjtu-cloud-audit-20261030/`。以下位置以修复后的工作树为准，触发条件和证据描述修复前行为。测试均使用临时文件、模拟桥或内存 provider，不涉及真实学习数据或真实云盘写操作。

## P0

未发现有证据支持的 P0。

## P1

1. **上传授权并发复用（已修复）** — `desktop_app.py:_drive_upload`：两个请求同时读取同一凭证，均在上传成功后才删除它，可将一次选择的文件上传两次或传到不同目录。`test_upload_capability_cannot_be_used_concurrently` 用线程和事件控制复现；现锁内认领凭证，失败仍允许重试，成功消费。
2. **登记后路径替换（已修复该窗口）** — `desktop_app.py:_drive_register_upload`、`_drive_upload`：登记后本地文件或父目录被替换，按路径重新打开会读取未经选择的对象。`test_upload_rejects_file_or_parent_replaced_after_registration` 与 `test_upload_uses_verified_handle_after_path_changes` 覆盖；现比较登记及打开句柄的设备/文件标识并直接传递该句柄。文件夹扫描到登记之间的更早窗口仍未解决，见下。
3. **上传校验失败后永久删除错误版本（已修复）** — `sjtu_learning_assistant/cloud_storage/sjtu_pan.py:_verify_confirmed_upload`：简单或分片确认后按路径永久删除大小不一致对象；若另一个任务已替换此路径，则删除的是新对象。校验过程中其他远端 API 错误也可进入同一清理分支。两项 `test_*_size_mismatch_does_not_delete_remote_path` 检查不发送 DELETE。现保留云端对象并报告需要检查；这可能留下不完整对象，无法在缺少条件删除协议时安全自动清理。
4. **下载覆盖未经确认的目标（已修复最终文件名窗口）** — `desktop_app.py:_drive_download`、`cloud_drive_service.py:TransferRegistry.commit_download`：选择器返回符号链接时原先 `resolve` 跟随链接；或目标在下载期间被其他进程创建，`os.replace` 均会覆盖目标。临时目录实证及 `test_save_target_symlink_does_not_overwrite_its_destination`、`test_download_does_not_overwrite_file_created_during_transfer` 覆盖。现仅解析父目录，使用同目录硬链接原子创建，不覆盖任何现有文件或符号链接；存在目标时要求另选名称。文件系统不支持硬链接时下载会安全失败；父目录被并发替换的更深层竞态仍需平台原语处理。
5. **文件夹扫描到登记之间的替换（未修复）** — `cloud_drive_service.py:iter_folder_files` 与 `desktop_app.py:_drive_upload_pick_folder`、`_drive_register_upload`：扫描跳过符号链接，但扫描与逐项登记之间攻击者仍可替换文件或父目录，登记会将替换对象当作初选对象。代码路径可证实，登记后的身份校验不能覆盖此窗口；需要跨平台固定目录句柄与身份的方案及实机测试。不要将本轮修复称作消除全部符号链接竞态。

## P2

1. **上传中源文件截短（已修复）** — `cloud_drive_service.py:_ProgressReader`：以初始大小限流，但提前 EOF 曾被当作正常结束；实际发送字节可能被确认，覆盖时造成不完整内容。`test_upload_rejects_source_truncated_after_open` 和 `test_simple_upload_rejects_truncated_read_all` 覆盖。
2. **取消与最终提交竞态（部分修复）** — `cloud_drive_service.py:TransferRegistry`、`_ProgressReader`、`download`；`desktop_app.py:_drive_prepare_download`、`_drive_download`；`driveTransfers.ts`：最后分片发送、下载落盘或保存对话框期间取消曾返回成功但操作继续。新增分片 EOF 检查、下载提交与取消同步、对话框前预登记以及排队/登记/弹窗时序回归。简单上传末次读取后 PUT 和确认之间没有取消钩子，取消仍可能返回成功而最终上传成功；网络确认已发出时无法保证撤销。
3. **目录失效后旧结果重新缓存（已修复）** — `cloud_drive_service.py:_listing`、`_invalidate`：列表请求进行中发生写操作，旧请求返回后可能重新填入缓存。`test_concurrent_invalidation_does_not_cache_old_listing` 通过事件协调复现；代数检查阻止旧结果入缓存。已经发出的列表请求仍可能短暂返回旧快照。
4. **意外上传异常未结束任务（已修复）** — `cloud_drive_service.py:upload`：非预期异常使任务一直显示运行中；`test_unexpected_upload_error_finishes_transfer` 验证终态。
5. **分片失败后本地签名缓存遗留（已修复常规整文件上传路径）** — `sjtu_pan.py:init_multipart`、`multipart_upload`：初始化或后续读取、取消、网络失败可遗留进程内分片签名，长期失败可累积。`test_multipart_failure_discards_cached_signatures` 覆盖后续失败；远端未确认分片的清理与过期行为仍需协议证据。
6. **传输历史裁剪丢失凭证和冲突任务（已修复）** — `driveTransfers.ts:trimFinished`：超过 60 条时失败上传凭证随记录丢失，无法释放；同时等待用户决定的冲突任务可被误裁剪。前端两项对应测试验证释放及保留。
7. **拖放目标切换竞态（已修复模拟事件路径）** — `useNativeFileDrop.ts`：DOM drop 发生在目录 A，原生事件晚于切换到 B 时曾调用最新回调，文件被排到 B。前端测试模拟该顺序，现绑定 drop 时的回调；打包版原生事件顺序仍需实测。

## 范围和剩余风险

- 基于代码与模拟测试，没有发现前端直接提交任意本地上传路径可绕过登记接口；这不是对所有桌面运行时或恶意同用户进程的完整安全证明。路径分段、文件名净化、异常脱敏等已有测试通过，不因缺乏实证而额外列为缺陷。
- 同一文件对象的内容可在选择后原地变化；设备/文件标识校验不能冻结内容。2 GiB 测试只模拟 `fstat` 边界，不读取 2 GiB 或验证真实性能。
- 仍需验证 Windows 打包版拖拽上传、保存对话框、在文件夹中显示、文件标识和硬链接支持；真实交大云盘账号上传、覆盖、删除、回收站恢复、未确认分片清理；数千文件目录性能。没有为此执行真实云盘写操作。
- 本轮没有构建或部署 App；现有安装包仍为交接所述 `c46bef9`，不含本轮修复。`codesign --verify --deep --strict` 和可执行文件 SHA-256 已核对；当前环境无法读取进程列表。

## 本轮验证

- 后端定向：`tests/test_cloud_drive_service.py tests/test_sjtu_pan.py`，68 项通过。最终完整 Python 测试：885 项通过、1 项跳过、324 个子测试通过；未设置外部测试数据库。
- 前端：50 个测试文件、535 项通过；TypeScript、Biome、Vite 生产构建通过。Vite 提示主 chunk 超过 500 kB。
- Python 编译检查、项目秘密模式扫描、Python 和 npm 许可证检查、`git diff --check` 通过。项目秘密扫描提示 7 个非 UTF-8 文件未扫描；本机没有 gitleaks 或 Docker，未运行 CI 的 gitleaks 容器扫描。
