# 日志脱敏统计器 使用说明

把下载引擎的日志压成一份**只含统计数字**的报告，用于分享、归档、排障。

34MB 的日志 → 约 1.3KB 的报告。日志正文永远不离开本机。

---

## 1. 放在哪里

脚本位置：`docs/log-sanitize/sanitize_log.py`（本仓库内）。

单文件、零依赖，只用 Python 标准库，复制到任何地方都能跑。报告输出到**运行时所在目录**下的 `log-reports/`，不会写进日志目录。

下文命令均假设站在仓库根目录执行。

---

## 2. 三种命令

### 单个日志文件

```bash
python docs/log-sanitize/sanitize_log.py "2026-09-05 10.00.00.log"
```

输出：`log-reports/2026-09-05 10.00.00.log.report.txt`

### 整个目录（推荐，会递归找所有 .log）

```bash
python docs/log-sanitize/sanitize_log.py "把日志复制到的文件夹"
```

传目录时会自动递归子目录，不用自己写通配符。**脱敏器不关心日志放在第几层**，只按文件内容逐行统计，所以复制过来时怎么套文件夹都行。

**引擎日志在哪：`<引擎目录>\_internal\Volume\Log\`**，文件名形如 `2026-09-05 03.50.38.log`。

注意不是引擎根目录。日志路径由 `src/custom/internal.py` 的 `PROJECT_ROOT`（= 代码上三级目录下的 `Volume`）决定，冻结版里代码在 `_internal` 下，所以是 `_internal\Volume\Log`。换引擎包后这个相对位置不变。

多于一个文件时会额外生成 `log-reports/_AGGREGATE.txt`，把所有报告拼在一起，方便一次性查看或粘贴。

### 通配符（只要某一批）

```bash
python docs/log-sanitize/sanitize_log.py "日志文件夹/**/*.log"
```

绝对路径也支持（直接读引擎原地日志，不必先复制）：

```bash
python docs/log-sanitize/sanitize_log.py "<引擎目录>/_internal/Volume/Log/2026-09-05*.log"
```

> 路径一定要用引号包起来。里面有空格或中文时，不加引号会被拆成多个参数。

---

## 3. 分享前必看

报告末尾有自检段：

```
[LEAK SELF-CHECK] all values below MUST be 0
  sessionid                0
  sid_tt                   0
  ...
RESULT: CLEAN
```

**只有 `RESULT: CLEAN` 才能分享。** 出现 `REVIEW NEEDED` 说明日志里有脚本没预料到的敏感字段，先别外发。

---

## 4. 报告里有什么 / 没有什么

**有**

| 段 | 内容 |
| --- | --- |
| 头部 | 文件名、字节数、行数、时间窗口、活跃分钟数 |
| `log levels` | INFO / WARNING / ERROR 计数 |
| `http status` | 各状态码计数与占比，**403 比率**（排障主要看这个）。比率把异常路径也算进分母 |
| `abnormal-path status` | `响应码异常` 行里提取的状态码。这些请求**不写** `Response Code:` 行，不在上一段的表里 |
| `endpoints seen` | 端点路径及次数（`aweme/post`、`im/user/info` 等），**查询串已丢弃**。只数请求行，不数响应行 |
| `field presence` | `a_bogus` / `x-secsdk-web-signature` / `msToken` / `uifid` 等字段**出现几次**，不含值。同样只数请求行 |
| `business keywords` | 提取、筛选、下载、跳过、重试、批量回退等中文关键词计数，含四项内容层失败（见下） |
| `LEAK SELF-CHECK` | 10 个高危字段必须全为 0 |

`business keywords` 里有四项专门用于"接口通了但内容没拿到"的失败，都发生在签名之后，值得单独看：

| 关键词 | 含义 |
| --- | --- |
| `url_parse_failed` | 返回 JSON 里播放地址字段为空，连下载地址都没拿到 |
| `download_interrupted` | 地址拿到了，传输中途断流 |
| `resp_code_abnormal` | 下载请求拿到非 2xx，真实状态码见 `abnormal-path status` |
| `private_account` | 私密账号，业务层拒绝（需登录且需关注），不是签名或接口问题 |

**没有**

Cookie、token、`sec_user_id`、作品 ID、昵称、作品标题、本地文件路径、URL 查询串、`x-tt-logid`。

---

## 5. 设计原则：白名单

脚本**不是**"把敏感内容替换掉"（黑名单），而是**只把事先认定安全的统计量写出来**（白名单）。

差别在于漏字段的后果：黑名单漏一个字段 → 敏感值直接进报告；白名单漏一个字段 → 报告少一项统计，不会泄露。

所以想加新指标时，要去 `sanitize_log.py` 顶部的常量里显式加，而不是放宽过滤。相关常量：

| 常量 | 作用 |
| --- | --- |
| `ENDPOINTS` | 要统计的接口路径 |
| `FIELD_PRESENCE` | 只统计出现次数的字段 |
| `FIELD_PRESENCE_LINES` | 限定在哪种行型上统计字段（避免请求/响应行双数） |
| `MUST_BE_ABSENT` | 自检用的高危字段，出现即告警 |
| `LEAK_PATTERNS` | 由 `MUST_BE_ABSENT` 编译出的词边界正则 |
| `KEYWORDS` | 中文业务关键词 |
| `ABNORMAL_CODE` | 从 `响应码异常` 行里抠出真实状态码的正则 |

---

## 6. 关于合并进管理程序结果页

后续要把它做进 DouK-Manager 的结果页时，这个脚本的结构已经为此留好了口子：

- `analyse(path) -> dict` 纯函数，返回结构化统计，**不做任何输出**
- `render(stats) -> str` 只负责把 dict 转成文本

也就是说，管理程序可以直接 `from sanitize_log import analyse`，拿到 dict 之后自己渲染成表格或图表，不必走文本报告这一层。

要接的话，建议这样：

1. 把 `sanitize_log.py` 复制到管理程序的包目录下（比如 `log_stats.py`）
2. 结果页在一次任务跑完后，对该次任务的引擎日志调用 `analyse()`
3. 页面上优先显示 **403 比率**和 `x-secsdk-web-signature` 出现次数——前者是健康度，后者能证明外挂签名确实在工作（为 0 说明退回了失效的 `a_bogus`）
4. `MUST_BE_ABSENT` 的自检结果建议也显示出来，作为"可安全分享"的标志

一个提醒：34MB 日志跑一次约几秒，逐行扫描。如果结果页要实时刷新，别每次重算，缓存 `analyse()` 的结果即可。

---

## 7. 已知边界

- 只识别引擎主日志（`_internal\Volume\Log\` 下的 `YYYY-MM-DD HH.MM.SS.log`）和管理程序的 `DownloadTask_*.log`。前者信息量大，后者主要是任务级摘要。
- 脱敏器不关心日志放在第几层目录，只按**文件内容**逐行统计。传目录时递归到任意深度，所以复制到别处时怎么套文件夹都行。
- `active_minutes` 是"有日志写入的分钟数"，不等于实际运行时长（暂停期间不写日志）。
- **端点与字段计数只统计请求行**（`[INFO]:  URL: ` / `[INFO]:  Params`），不统计 `Response URL: `。引擎对同一次请求会先记请求 URL 再记响应 URL，两边都数会让每个数字恰好翻倍。可用这个恒等式自查口径：各端点次数之和 = `Params` 行数 = `Response Code: 200` 计数。
- **`a_bogus` 与 `x-secsdk-web-signature` 计数相同是正常的**，不代表降级。`encipher.py` 的 `sign_url` 保留 `a_bogus` 后再追加签名。判断外挂签名是否生效只看 `x-secsdk-web-signature`——为 0 才是退回了失效的本地算法。
- **`suspend()` 不写日志**（走 `console.print`），所以关键词表里刻意没有它，日志中也查不到暂停记录。是否触发按"账号数 ÷ 批量大小"推算。
- HTTP 状态码统计来自 `Response Code:` 行。引擎只在成功拿到响应时记录，连接层失败（超时、DNS）不计入，那些体现在 `ERROR` 计数里。
- **下载请求的失败状态码不在 `[http status]` 里。** 引擎对下载走 `httpx` 的异常路径，只记 `HTTPStatusError` 文本、不写 `Response Code:` 行。所以像媒体 404 这类失败在主表里查不到（实测 `Response Code: 404` 为 0，但日志里确有 2 次 404）。这些码单列在 `[abnormal-path status]`，`403 比率` 的分母已把它们算进去。看整体健康度要两段一起看。
