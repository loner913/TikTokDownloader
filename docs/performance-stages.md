# DouK-Manager 定制提速阶段与验收记录

本文记录 `loner913/TikTokDownloader` 中已经验收的 DouK-Manager 定制提速版本。
它不替代上游项目说明，也不表示 `master`、上游 Release 或任意独立启动方式自动包含或
启用这些优化。

## 版本身份与继承关系

| 阶段 | 分支 | 固定提交 | 关系与状态 |
| --- | --- | --- | --- |
| 文档基线 | `master` | `3d8696a1b1d896e6b6f6c26cd48f7c43a7f78d77` | 不含两阶段定制提速代码 |
| 第一阶段 | `perf/user-link-regex-shortcircuit` | `0b88ed875558064be026952fb98cd2b3174a4047` | 从本仓库 `master` 历史开发并验收冻结 |
| 第二阶段 | `perf/account-info-batch-fallback` | `34ae30fb6c4c2f6468ee777e649e3d20277280a8` | 线性继承第一阶段，已验收冻结 |

第二阶段包含第一阶段的全部功能。后续性能开发应从第二阶段固定提交另开分支，不得移动、
改写或强推上述两个冻结分支。

准确入口：

- [第一阶段冻结分支](https://github.com/loner913/TikTokDownloader/tree/perf/user-link-regex-shortcircuit)
- [第一阶段固定提交](https://github.com/loner913/TikTokDownloader/commit/0b88ed875558064be026952fb98cd2b3174a4047)
- [第二阶段冻结分支](https://github.com/loner913/TikTokDownloader/tree/perf/account-info-batch-fallback)
- [第二阶段固定提交](https://github.com/loner913/TikTokDownloader/commit/34ae30fb6c4c2f6468ee777e649e3d20277280a8)
- [手动“构建可执行文件”工作流](https://github.com/loner913/TikTokDownloader/actions/workflows/Manually_build_executable_programs.yml)

`master` 页面及上游 `JoeanAmier/TikTokDownloader` 的 Release/下载入口不能作为已安装定制
版本的身份依据。正确构建来源是上述手动工作流明确选择
`perf/account-info-batch-fallback`，并确认签出的源码提交严格为第二阶段固定 SHA。

## 第一阶段：账号长链接本地解析优先

在 Douyin 账号入口处理标准长链接时，先从
`https://www.douyin.com/user/<sec_user_id>` 本地解析并严格校验账号标识。命中后省去
原链接解析 GET 及该请求对应的 `wait()`；结果未命中、不确定、不合法或解析发生异常时，
完整回退原 GET 路径。

短链接继续走原 GET 路径。TikTok、作品详情、合集、直播、监听等非目标入口没有被扩展为
本地账号长链接快捷路径。快捷路径只减少可证明冗余的主页解析请求，不改变后续 Info、
作品分页、日期筛选、下载和查重逻辑。

## 第二阶段：20 账号 Info 批量预取

第二阶段只改变 DouK-Manager 启动的 Douyin 多账号批量入口。引擎通过以下三个环境变量均
存在来识别 Manager 集成环境：

- `DOUK_MANAGER_BACKUP`
- `DOUK_ACCOUNT_BATCH_SIZE`
- `DOUK_ACCOUNT_REST_SECONDS`

DouK-Manager 仍以既有 `5 1 1 Q` 命令启动账号批量下载，并传入上述隔离、账号暂停批次和
暂停秒数。普通独立运行若没有同时具备这些 Manager 环境标记，继续走原单账号 Info 路径；
TikTok 多账号入口即使存在这些变量也保持原路径。因此不能把“运行任意本仓库 EXE”描述为
一定启用批量 Info。

目标入口按最多 20 个 `sec_user_id` 进行一次 Info 预取。返回记录使用记录自身的
`sec_uid` 建立映射，绝不按响应顺序、数组下标或 `zip(requested, returned)` 配对。
只有请求集合中的、唯一且结构可靠的记录才会进入下游；原有下游身份核对仍然保留。

以下情况不会静默丢账号或错误配对，而是让受影响账号使用原单账号 Info：

- 返回缺失、重复、额外或顺序变化；
- 空 ID、畸形记录或身份字段不完整；
- 整批请求或响应结构失败；
- 链接无法可靠提取 `sec_user_id`；
- 其他无法证明预取记录属于当前账号的情况。

`Info.run()` 的默认单账号行为保持兼容；完整列表只由批量路径显式请求。一次成功的 Info
批量对应一次逻辑请求和一次 `wait()`，不是每个账号各等待一次。

## Info 批量与暂停边界

Info 批量上限 20 和 Manager 账号暂停配额 50 是两个不同设置：

- `20`：一次 Info 预取的内部最大账号数；
- `50`：当前正式 Manager 在暂停前处理的账号配额；
- `30`：到达账号暂停边界后的暂停秒数。

Info 预取不得跨过下一次暂停边界。因此一个完整 50 账号配额会切为 `20 + 20 + 10`，
而不是单次请求 50 个 Info。尾部不足 20 时按实际剩余数量请求。若 Manager 将来传入不同
暂停配额，引擎仍按实际边界切分，不会提前预取边界之后的账号。

## 保留的行为范围

两阶段实现保持以下既有语义：

- 账号任务、A 编号和重复任务的原始顺序；
- 返回身份字段及下游身份校验；
- UID/mark 命名和账号目录归属；
- 作品分页、`earliest`/`latest` 日期筛选和文件名生成；
- 下载、数据库查重、已有文件跳过和断点处理；
- Manager 的账号暂停配额及暂停秒数。

批量预取是 Info 数据来源的快捷路径，不是并发处理多个账号，也不会跳过每个账号后续的
分页、筛选、下载或查重流程。

## 已完成验证

### 源码与隔离验证

- 第一阶段历史交接记录记载：引擎测试 `31 passed, 18 subtests passed`，并以历史日志对
  1,331 个标准长链接完成有序解析回放。该记录是开发交接材料，不是最终验收报告。
- 第二阶段冻结前的工作区原始测试输出为 `57/57`，覆盖第一阶段快捷路径回归、5/10/20
  离线批量规模、响应乱序、重复/缺失/额外/畸形返回、整批失败、单账号回退、暂停边界、
  下游参数一致性以及每批一次请求/一次 `wait()`。
- 真实 Info 探针依次通过 baseline 3、capacity 5、10 和 20；capacity 20 返回 20 个唯一
  可映射记录，身份集合匹配，无回退，每批一次逻辑 Info 请求和一次 `wait()`。
- 21 账号隔离 Manager 验收形成 `20 + 1` 两批，账号顺序完整，主页长链接 GET 为 0，
  短链接保留 GET 回退，并产生实际下游作品结果。

### GitHub Actions 最终产物身份

最终构建来自仓库现有“构建可执行文件”手动工作流 Run #5，源码分支为
`perf/account-info-batch-fallback`，源码提交为第二阶段固定 SHA。已下载产物完成只读 ZIP
哈希、CRC、路径安全、`main.exe`/`_internal` 结构检查，并在 D 盘隔离环境完成身份对齐。

| 对象 | 文件名/相对路径 | 大小（字节） | SHA-256 |
| --- | --- | ---: | --- |
| Windows X64 Artifact | `DouK-Downloader_Windows_X64_20260830` | - | - |
| 下载 ZIP | `DouK-Downloader_Windows_X64_20260830.zip` | 25,323,539 | `DA8442E38F97027A9DA7B0D57FE1B7DAF9F664049DD1947504A4ECE125D234C3` |
| ZIP 内最终 EXE | `main.exe` | 9,905,823 | `314C94A63668C41E09CC30F13A969B688C71B65D0208AECCA9C885316FC122F8` |

最终 Actions `main.exe` 与此前 9,899,100 字节的本地候选不是同一文件；正式候选身份必须以
上表 Actions 产物为准，不得混用本地候选的 `_internal` 或 Volume。

最终 Actions EXE 复用隔离账号集合完成 capacity 20 身份集合断言，并完成 21 账号
`20 + 1` Manager 边界验证；Cookie 在验收后清空。原始 Cookie、账号链接、真实 ID、日志、
数据库和媒体不属于仓库文档证据。

### 50 账号边界与 9 月 1 日全量运行

以下两轮摘要由工作区保留的 9 月 1 日任务日志、原生日志和用户确认结果共同核对。两轮都
使用 Manager 的 50 账号/30 秒暂停设置，标准账号主页 GET 为 0。

| 指标 | 第二阶段升级前 | 第二阶段全量 |
| --- | ---: | ---: |
| 计划账号 | 1,412 | 1,406 |
| 标准账号主页 GET | 0 | 0 |
| Info 逻辑请求 | 1,412 | 85 |
| 作品分页尝试 | 1,757 | 1,748 |
| 引擎报告耗时 | 6 小时 13 分 59 秒 | 3 小时 52 分 03 秒 |

第二阶段 85 次 Info 由 `56 批 x 20 + 28 批 x 10 + 1 批 x 6` 组成，合计覆盖
1,406 个账号。`20 + 20 + 10` 的 50 账号边界重复 28 次，运行序号连续并出现 28 次
30 秒暂停；最后 6 个账号形成尾批。引擎最终报告成功 1,404、失败 2，两个失败均为私密
账号。未观察到批量 Info 导致的错序、身份错配、额外单账号 Info 回退或任务中断。

Info 请求从 1,412 降至 85，减少约 94%，直接达到第二阶段减少 Info 往返和对应等待的
设计目标。整轮耗时观察下降约 38%，但两轮账号集合、已有文件和实际下载量不同，不能把
该数值写成严格同条件的净提速率。全量验收通过也不等于每个账号的所有作品都完成下载；
它证明的是本轮计划账号处理、批量边界和关键下游链路按验收范围完成。

Manager 结果汇总另有 35 项 mark 清洗兼容问题，在升级前后均存在，已登记为
[独立已知问题](https://github.com/loner913/DouK-Manager/blob/docs/account-summary-known-issue/docs/known-issues/account-summary-mark-normalization.md)。
它属于 Manager 汇总层，不是第二阶段批量 Info 缺陷，不推翻上述验收结论。

## 历史交接材料

第一阶段的 `CODEX_HANDOFF.md` 保留在第一阶段固定历史中：
[查看固定版本](https://github.com/loner913/TikTokDownloader/blob/0b88ed875558064be026952fb98cd2b3174a4047/CODEX_HANDOFF.md)。
该文件明确是当时的原始交接说明，包含待独立核验项，不能当作本文件所记录的最终验收结论，
也不应被改写来覆盖其历史语境。
