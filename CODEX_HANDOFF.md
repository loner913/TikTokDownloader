# Codex 交接说明：账号长链接正则短路

> **本文件性质**：这是 Claude 的交接说明，**不是最终验收结论**。
> Codex 必须根据源码、diff 和测试独立裁定，不应把本文件的任何结论当作既定事实。
> 文中所有测试结果均为交接前已实际运行所得，未为撰写本文件重新运行任何测试。

## 1. 仓库与基准

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/loner913/TikTokDownloader.git` |
| 基准分支 | `master` |
| 基准 SHA | `3d8696a1b1d896e6b6f6c26cd48f7c43a7f78d77` |
| 开发分支 | `perf/user-link-regex-shortcircuit` |
| 产品提交完整 SHA | `58a51f11f9d5833480360942dc9600440b788230` |
| 产品提交相对基准 | 1 个提交，2 个文件 |
| 上游参考（只读，未合并） | `JoeanAmier/TikTokDownloader` @ `d3806386b392da7341397e18522acdd5283f2c81` |

基准 `master` 即上游 tag `5.7` 之后的 fork 状态，`3d8696a` 为 fork 自有提交
「支持管理器动态设置账号批次与暂停时间」。

## 2. 本次实际需求

**标准账号长链接正则优先，未命中继续原 GET。**

`5-1-1` 批量账号模式下，`accounts_urls` 中每个 URL 原本都要先发一次 GET
解析跳转后才能得到 `sec_user_id`。标准长链接
`https://www.douyin.com/user/<sec_user_id>` 本身已含 ID，无需跳转。
改为本地正则优先：命中且校验合法则跳过该次 GET；正则未命中、结果不合法、
链接类型不确定或解析异常，一律回退到原有 GET 路径。

动机：真实运行日志实测 1331 次此类 GET **全部零重定向**（请求 URL 与响应 URL
的 `sec_user_id` 逐项相同），且每次附带一次 `wait()`（实测均值约 6 s）。

短链接（`v.douyin.com`）与后台监听场景**继续走原 GET**，未做任何削减。

## 3. 实际修改的文件、函数及调用链

产品文件（唯一）：`src/link/extractor.py`

| 符号 | 变更 |
|---|---|
| `Extractor.run()` | 修改，函数体开头加入 2 行短路判断 |
| `Extractor._valid_account_url()` | 新增，`classmethod`，本地校验并返回 `sec_user_id` |
| `Extractor.__account_shortcut()` | 新增，私有（name mangling 为 `_Extractor__`） |
| `ACCOUNT_LINK_HOST` / `ACCOUNT_LINK_PATH` / `ACCOUNT_ID_MIN_LENGTH` | 新增类常量 |
| `from re import compile` | 改为 `from re import compile, fullmatch` |

测试文件（新增）：`tests/test_account_link_shortcut.py`

调用链（`5-1-1` 批量账号模式）：

```
main_terminal.py:354  account_detail_batch
  → :374  __account_detail_batch          循环 accounts
    → :386  check_sec_user_id(data.url, tiktok)
      → :427  self.links.run(sec_user_id, "user")     ← Extractor.run，本次唯一改动点
        → 命中：__account_shortcut 返回 [sec_user_id]，不调用 requester
        → 未命中：await self.requester.run(text, proxy)  ← 原路径，未修改
                   → requester.py:41 request_url + :47 await wait()
    → :401  deal_account_detail            未修改
      → :564  get_user_info_data           Info 接口，未修改
      → :587  _get_account_data            分页 + early_stop，未修改
      → :606  _batch_process_detail        下载 + 查重，未修改
    → :412  suspend(index, self.console)   50/30，未修改
```

短路仅在 `type_ == "user"` 时进入。`detail` / `mix` / `live` / `""` 分支与
`ExtractorTikTok.run()`（该类有自己的 `run`，见 `extractor.py:245`）完全走原路径。
后台监听 `main_monitor.py:127` 调用 `link_object.run(url)` 不传 `type_`，
默认值为 `"detail"`，不进入新分支。

## 4. 产品代码 diff 摘要

```
 src/link/extractor.py               |  58 ++++++-
 tests/test_account_link_shortcut.py | 292 ++++++++++++++++++++++++++++++++++++
 2 files changed, 349 insertions(+), 1 deletion(-)
```

`Extractor.run()` 的全部改动即以下 2 行：

```python
if type_ == "user" and (shortcut := self.__account_shortcut(text)):
    return shortcut
```

`_valid_account_url()` 用 `urlparse` 拆解后逐项核对：`scheme == "https"`、
`netloc == "www.douyin.com"`、路径恰好 2 段且首段为 `user`、ID 长度 >= 16、
ID 字符集 `fullmatch(r"[A-Za-z0-9_-]+")`。任一不满足返回空字符串。

`__account_shortcut()` 用 `Requester.URL` 切出文本中的每个 URL，**逐 URL 独立校验**，
任一 URL 不通过即返回空列表（整体回退，不做部分短路）；最后再与现有
`user()` 的返回结果比对，不一致也回退；整个函数体包在 `try/except Exception` 中，
任何异常均回退。未新增任何比现有 `account_link` / `account_share` 更宽的账号正则。

`git diff master..HEAD` 显示改动仅限上述 2 个文件，无格式化、无无关重构。

## 5. 已运行的测试、精确命令与真实结果

以下均为交接前实际运行结果，逐字抄录，未为本文件重新运行。
所用解释器为工作区隔离 venv，**不在本仓库内**（见第 7 节）。

### 5.1 引擎测试套件

```bash
cd TikTokDownloader-fork
python -m pytest tests/ -q
```

结果：`31 passed, 18 subtests passed in 0.65s`

含原有 `tests/test_manager_suspend.py` 的 7 项（50/30 环境变量行为回归）
与本次新增 `tests/test_account_link_shortcut.py` 的 24 项。

### 5.2 Manager 测试套件

```bash
cd DouK-Manager
PYTHONPATH=src python -m pytest tests/ -q
```

结果：`56 passed, 2 skipped, 52 subtests passed in 1.63s`

与改动前一致；2 项 skip 为改动前既有。
注：不加 `PYTHONPATH=src` 会因 `src/` 布局报
`ModuleNotFoundError: No module named 'douk_manager'`，属环境问题非测试失败。
Manager 仓库产品代码本次**未做任何修改**。

### 5.3 1331 个真实账号有序一致性

脚本 `verify_1331_parity.py`（在仓库外，见第 7 节）。数据源为用户提供的
已脱敏 `Volume/settings.json` 与真实运行日志 `2026-08-11 15.59.30.log`，
基线取自日志中每次链接解析 GET 的 `Response URL`，按日志顺序**有序逐项**比较。

```
启用账号数（settings.json）      : 1331
基线 GET 响应数（真实日志）      : 1331
短路提取结果数                  : 1331
链接解析 GET 调用次数            : 0

[PASS] 有序逐项一致: 1331/1331 全部相同
[PASS] 链接解析 GET 次数为 0
[PASS] 全部账号均提取到 sec_user_id，无空结果
[PASS] 去重语义一致: distinct=1331
[PASS] 计数语义一致: 每个账号恰好一条结果 (1331)
```

### 5.4 下游调用未减少

脚本 `verify_downstream_unchanged.py`（在仓库外）。以替身统计各环节调用次数：

```
环节                    基线      短路   判定
链接解析 GET               1       0   OK (预期减少)
Info 接口                1       1   OK (未减少)
作品分页请求                 3       3   OK (未减少)
日期早停回调                 3       3   OK (未减少)
下载调用                   1       1   OK (未减少)
查重调用                   1       1   OK (未减少)
```

短链接场景：链接解析 GET = 1，Info = 1，分页 = 3，解析结果与长链一致。

**重要限定**：本脚本用替身模拟下游环节的调用次数，**不是**真实执行 Info、
分页与下载。它证明短路不改变调用序列的结构，**不能**替代真实网络验证。

### 5.5 Manager 日志解析协议

脚本 `verify_manager_log_contract.py`（在仓库外）。对真实日志 112330 行，
用 DouK-Manager `origin/develop:src/douk_manager/core/download_summary.py`
的 5 条正则比对：

```
_START_RE               1331
_LOGGED_MARK_RE         1237
_FILTERED_RE            1237
_TOTAL_RE               6672
_RUN_ANCHOR_RE             1

短路后将不再产生的链接解析行数（样本上限 400）: 2662
[PASS] 检查 400 行样本，0 处与 Manager 正则冲突
[PASS] 账号编号锚点存在: 开始处理=1331, 运行锚点=1
```

即命中短路后消失的 4 类行（`URL:` / `Response URL:` / `Response Code:` /
`Response Headers:`）不被 Manager 任何一条正则匹配。

### 5.6 编译与导入

```bash
python -m py_compile src/link/extractor.py tests/test_account_link_shortcut.py
```
两文件均通过编译。`src.link.extractor` 与 `src.link.requester` 导入正常。

`src.application.main_terminal` 在所用隔离 venv 中导入失败：
`ModuleNotFoundError: No module named 'gmssl'`。**该现象改动前即存在**，
与本次改动无关（`gmssl` 未装入该 venv）。

### 5.7 未执行的静态检查

`ruff` 未安装于所用隔离 venv，**本次未执行 lint**。

## 6. 离线测试 vs 未验证项

### 6.1 全部为离线测试

第 5 节所有结果均为**离线**取得：

* 未携带 Cookie 请求抖音任何接口；
* 未下载任何媒体文件；
* 未进行真实风控压力测试；
* 1331 账号一致性用的是**历史日志**作基线，不是新发请求；
* 下游调用统计用的是**替身**，不是真实 Info/分页/下载。

用户提供的 `Volume/settings.json` 中 `cookie` 字段长度为 0（脱敏时已清空），
本会话从未读取、复制、输出或使用任何 Cookie 或 Token 值。

### 6.2 尚未做 Windows / Manager / 真实网络验证

以下项目**一律未验证**，不得视为通过：

| 项目 | 状态 |
|---|---|
| 独立引擎实机运行，标准长链接不产生链接解析 GET | **未验证** |
| 短链接实机走 GET 并正常解析 | **未验证** |
| 后台监听模式实机启动/停止/重启 | **未验证** |
| Manager 批量启动、账号编号、日志、结果页面 | **未验证** |
| 50/30 在真实运行中于第 50 个边界触发一次 | **未验证** |
| Info、分页、下载、查重在真实网络下正常执行 | **未验证** |
| 打包后 EXE 的运行时行为 | **未验证**（EXE 已构建但从未运行） |
| 真实网络条件下的耗时收益 | **未验证** |

**不得**根据本文件宣称任何固定提速比例。此前同类 10.31 小时负载的理论参考
约为 7.2 小时，该数字**仅为基于历史日志的推算**，必须以同等工作量实机运行确认。

### 6.3 已知但本次未修复的缺陷

`src/interface/template.py:232-243` 的 `check_response` 在数据为空或 `KeyError`
时设 `self.finished = True`；`src/tools/retry.py:21-22` 在重试耗尽时同样设
`self.finished = True`。二者与"正常翻完分页"的终止状态**无法区分**，
因此分页异常或重试耗尽可能被公共链路判为完成，部分成功的账号仍可能计入成功。

**这是上游已有的独立缺陷，本次未修复，也未声称解决。**
新增测试 `KnownDefectNotWorsenedTests` 仅确认短路未使其恶化：短路只在本地校验
完全通过时返回结果，GET 异常照旧向上抛出，不会被吞成"成功但空结果"。

另一项已知限定：`DATA_HEADERS`（`src/custom/internal.py:64`）本身不含 `Cookie` 键，
显式 Cookie header 由 `parameter.py` 的 `set_headers_cookie` 在有 Cookie 时写入。
有 Cookie 时显式 header 优先于 httpx cookie jar（已用 httpx 0.28.1 MockTransport
离线实测），跳过 GET 无影响；**完全无 Cookie** 的运行下 jar 会生效，此时跳过 GET
会少 2 个 jar cookie。该场景本就取不到私密内容，未做处理。

## 7. 已生成的测试与构建产物（全部在仓库外）

| 产物 | 绝对路径 | 是否在仓库内 |
|---|---|---|
| 隔离测试 EXE | `D:\AI-Projects\DouK-Manager-Claude\_test_runtime\build\dist\main\main.exe` | **否** |
| PyInstaller 工作目录 | `..\_test_runtime\build\work\` | **否** |
| PyInstaller spec | `..\_test_runtime\build\spec\`（已移出仓库） | **否** |
| 构建用 venv (Py 3.12.13) | `..\_test_runtime\build\venv312\` | **否** |
| 测试用 venv | `..\_test_runtime\venv\` | **否** |
| pip 缓存 | `..\_test_runtime\pipcache\` | **否** |
| 验证脚本 3 个 | `..\_test_runtime\verify_*.py` | **否** |

EXE 构建命令（与仓库 CI `.github/workflows/Manually_build_executable_programs.yml`
一致，仅追加 `--distpath` / `--workpath` 以隔离输出）：

```bash
pyinstaller --icon=./static/images/DouK-Downloader.ico \
  --add-data "static:static" --add-data "locale:locale" \
  --collect-all rich main.py
```

构建自 `58a51f1`，产物 9,895,868 字节。**该 EXE 从未运行过。**
正式 EXE 未被替换，未发布，未创建 Release 或 Tag。

以上产物**均未提交入库**，`.gitignore` 与 `git ls-files` 已确认
`Volume/`、`_test_runtime`、`dist`、`build`、`main.spec` 全部未被跟踪。

## 8. 仍需 Codex 独立验证的项目

1. **源码正确性独立复核**：`_valid_account_url()` 的校验条件是否存在漏放行
   （尤其非 `www.douyin.com` 的同后缀域名、路径段数、字符集边界）。
2. **短路与 `user()` 结果比对是否多余或必要**：当前实现在本地校验通过后
   仍与 `user()` 比对一次，Codex 应判断这层冗余是否应保留。
3. **整体回退策略是否符合预期**：混合文本中任一 URL 未通过即全部回退，
   而非逐 URL 混合处理。这是有意为之（避免结果顺序错位），需确认可接受。
4. **`except Exception` 的宽度**：是否应收窄为具体异常类型。
5. **第 6.2 节全部未验证项**的实机验证。
6. **测试替身的代表性**：`verify_downstream_unchanged.py` 用替身统计调用次数，
   Codex 应判断是否需要以真实链路复测。
7. **`ruff` / 项目既有 lint 规则**下本次改动是否合规（本次未执行）。
8. **是否接受 `gmssl` 缺失导致 `main_terminal` 无法导入**的测试环境限制。

## 9. 最小实机验收建议

> 目的是以最小代价确认改动在真实环境下无回归，**不是**完整回归验收。

1. **2～3 个标准账号长链接通过 Manager 运行**
   在 `settings.json` 的 `accounts_urls` 中仅保留 2～3 条
   `https://www.douyin.com/user/<sec_user_id>` 长链接，通过 Manager 正常启动，
   确认引擎日志中**不再出现** `URL: https://www.douyin.com/user/...` 及其
   `Response URL/Code/Headers` 行。
2. **1 个需要 GET 兜底的短链接或监听场景**
   用 `v.douyin.com` 短链接（或后台监听模式投入一条含短链接的文本），
   确认日志中**仍然出现**链接解析 GET，且解析结果正确。
3. **检查结果页、账号编号、输出文件和错误提示**
   确认 Manager 结果页分类正常、账号编号（`开始处理第 N 个账号`）连续、
   下载文件落盘正常、失败账号的错误提示与改动前一致。

明确**不做**：

* 不重新跑 1331 个账号完整下载；
* 不用 51 个真实账号重新验证 50/30；
* 不重新验收整个 Manager V0.1.5。

补充提示：`suspend()` 通过 `console.print`（`src/custom/function.py:92`）输出，
**不写入日志文件**，因此日志中永远搜不到「暂停运行」字样。
若需确认 50/30，只能观察账号间时间戳的间隔跳变，不能 grep 日志文本。

## 10. 回滚方式

本次改动为单一提交、单一产品文件，回滚成本极低。

回滚整个改动（回到基准）：

```bash
git checkout master
```

仅撤销产品提交而保留分支：

```bash
git revert 58a51f11f9d5833480360942dc9600440b788230
```

只还原产品文件、保留测试：

```bash
git checkout 3d8696a1b1d896e6b6f6c26cd48f7c43a7f78d77 -- src/link/extractor.py
```

运行时无需回滚：本次未新增任何开关、环境变量或配置项，
`master` 的 EXE 与本分支的 EXE 可直接互换，`settings.json` 无需改动。

## 11. 本次冻结边界（均未开发、未修改）

| 冻结项 | 状态 |
|---|---|
| `5-1-4` 快速批量模式 | **未开发**（无新菜单、新模式、新环境变量、新 Manager 协议） |
| Manager 产品代码 | **未修改**（`DouK-Manager` 仓库 `git status` 为空） |
| `wait()` 与 `avg_delay=6s` | **未修改**（`src/custom/function.py` 不在 diff 中） |
| 现有 50/30 机制 | **未修改**（fallback 仍为 50/150，环境变量、账号计数、`suspend` 位置均原样） |
| Info、分页、日期筛选、下载链路 | **未修改**（`main_terminal.py`、`template.py`、`account.py` 不在 diff 中） |
| 代理、Cookie、断点续传 | **未修改**、未开发 |
| 上游 21 个提交 | **未合并**（未 fetch/merge/rebase；upstream 仍为 `d380638`） |
| 公共链路原有分页/假完成缺陷 | **未修复**（见 6.3） |
| 菜单编号与 `run_command` | **未修改**（仍为 `5 1 1 Q`） |
| `Requester.run()` 的 GET 行为 | **未改写**（`src/link/requester.py` 不在 diff 中） |

---

**再次声明**：本文件是 Claude 的交接说明，不是最终验收结论。
第 6.2 节列出的所有项目均未验证，Codex 必须依据源码、diff 与独立测试自行裁定。

