"""下载引擎日志脱敏 + 统计工具。

用途：把引擎日志压成一份只含"统计数字与行型"的报告，
不输出任何 Cookie、token、账号标识、作品标识、昵称、本地路径。

用法：
    python sanitize_log.py <日志文件 | 目录 | 通配符> ...

    单个文件：  python sanitize_log.py "2026-09-05 10.00.00.log"
    整个目录：  python sanitize_log.py "<引擎目录>/_internal/Volume/Log"
    通配符：    python sanitize_log.py "Log/*.log"

引擎日志位置：<引擎目录>/_internal/Volume/Log/（由 src/custom/internal.py 的
PROJECT_ROOT 决定，不在引擎根目录）。传目录会递归到任意深度。

输出：全部写到 ./log-reports/ 下，不污染日志目录。
    <日志名>.report.txt   每个输入文件一份
    _AGGREGATE.txt        两个及以上文件时的汇总

设计原则：白名单。只有明确认定安全的统计量才会被写出，
不做"黑名单替换"——那样容易漏掉没预料到的字段。
"""

from collections import Counter
from pathlib import Path
from re import IGNORECASE, compile as re_compile
from sys import argv, stdout

OUTPUT_DIR = Path("log-reports")


def _say(message: str) -> None:
    """Windows 控制台 GBK 下也能安全输出。"""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = stdout.encoding or "ascii"
        print(message.encode(encoding, "replace").decode(encoding), flush=True)

# ---------- 行型识别（只用于计数，不保留内容） ----------

TS = re_compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
LEVEL = re_compile(r"\[(INFO|WARNING|ERROR|DEBUG|CRITICAL)\]")

# 需要统计的端点（只记路径，不记查询串）
ENDPOINTS = (
    "aweme/v1/web/aweme/post/",
    "aweme/v1/web/aweme/detail/",
    "aweme/v1/web/im/user/info/",
    "aweme/v1/web/mix/aweme/",
    "aweme/v1/web/aweme/favorite/",
)

RESP_CODE = re_compile(r"Response Code:\s*(\d{3})")

# 只统计这些字段"是否出现"，绝不记录其值。
#
# 同样只在请求行（`[INFO]:  Params` / `[INFO]:  URL: `）上统计。若把
# `Response URL: ` 也算进来，每个字段会翻倍（实测签名 917 次被记成 1,834），
# 与 `[endpoints seen]` 的口径不一致，容易被误读成"有请求没签名"。
#
# 注意 a_bogus 与 x-secsdk-web-signature 计数相同属正常：encipher.py 的
# sign_url 刻意保留 a_bogus 再追加签名，不是降级。判断外挂签名是否生效
# 只看 x-secsdk-web-signature。
FIELD_PRESENCE_LINES = ("INFO]:  Params", "INFO]:  URL: ")

FIELD_PRESENCE = (
    "a_bogus",
    "x-secsdk-web-signature",
    "msToken",
    "uifid",
    "webid",
    "screen_width",
    "screen_height",
    "device_platform",
    "version_code",
)

# 出现即视为高危，必须为 0（用于自检脱敏是否漏了东西）
#
# 用词边界匹配，不用子串匹配。原因：CDN 下载链接里带 `kssessionid=`
# （快手边缘节点会话参数）、`uid_tt` 也可能作为别的参数的后缀出现，
# 子串匹配会把它们误判成登录态，导致每次都报假的 REVIEW NEEDED。
# 边界定义为“前面不是字母数字下划线”，因此 `kssessionid` 不再命中，
# 而真正的 `sessionid=` / `'sessionid'` / `; sessionid` 仍然命中。
MUST_BE_ABSENT = (
    "sessionid",
    "sid_tt",
    "sid_guard",
    "passport_csrf_token",
    "passport_auth_token",
    "sso_uid_tt",
    "uid_tt",
    "s_v_web_id",
    "verifyFp",
    "Authorization",
)

LEAK_PATTERNS = {
    field: re_compile(r"(?<![A-Za-z0-9_])" + field, IGNORECASE)
    for field in MUST_BE_ABSENT
}

# 业务关键词（中文日志行），只计数
KEYWORDS = {
    "account_start": "开始处理账号",
    "extract_start": "开始提取作品",
    "filtered": "筛选处理后作品",
    "cache_update": "更新缓存数据",
    "download_start": "开始下载作品",
    "skip_video": "跳过视频作品",
    "skip_image": "跳过图集作品",
    "skip_live": "跳过实况作品",
    "retry": "重试",
    "failed_extract": "链接提取失败",
    "batch_fallback": "回退单账号路径",
    "batch_uncertain": "批量账号信息存在不确定返回",
    "cookie_invalid": "Cookie",
    # 以下四项是"接口通了但内容没拿到"的失败，全部发生在签名之后。
    # 它们不出现在 [http status] 里，必须单独数，否则报告会给人
    # "一次非 200/206 都没有"的错觉。
    "resp_code_abnormal": "响应码异常",
    "download_interrupted": "下载中断",
    "url_parse_failed": "视频下载地址解析失败",
    "private_account": "私密账号",
}

# 从 `响应码异常` 行里提取真实状态码。
#
# 引擎对下载请求走 httpx 的异常路径，只记 HTTPStatusError 文本，**不写
# `Response Code:` 行**。所以媒体 404 之类的失败在 [http status] 里完全
# 看不到（实测：`Response Code: 404` 为 0，但日志里确有 2 次 404）。
# 这个正则把它们捞回来，单列一段。
ABNORMAL_CODE = re_compile(r"Client error '(\d{3})|Server error '(\d{3})")

# 不要为 suspend() 加关键词。它走 console.print（见 src/custom/function.py:92），
# 只打到控制台、不写日志文件，因此日志里永远是 0 次。放进来会让人误读成
# "节流没生效"。是否触发请按账号数 ÷ 批量大小推算，不要从日志找证据。


def analyse(path: Path) -> dict:
    stats = {
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "lines": 0,
        "levels": Counter(),
        "http_codes": Counter(),
        "abnormal_codes": Counter(),
        "endpoint_urls": Counter(),
        "field_presence": Counter(),
        "leak_check": Counter(),
        "keywords": Counter(),
        "minutes": Counter(),
        "first_ts": None,
        "last_ts": None,
    }

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stats["lines"] += 1

            if matched := TS.match(line):
                ts = f"{matched.group(1)} {matched.group(2)}:{matched.group(3)}"
                if stats["first_ts"] is None:
                    stats["first_ts"] = ts
                stats["last_ts"] = ts
                stats["minutes"][ts] += 1

            if matched := LEVEL.search(line):
                stats["levels"][matched.group(1)] += 1

            if matched := RESP_CODE.search(line):
                stats["http_codes"][matched.group(1)] += 1

            # 异常路径的状态码（不写 Response Code: 行，见常量处说明）
            if matched := ABNORMAL_CODE.search(line):
                stats["abnormal_codes"][matched.group(1) or matched.group(2)] += 1

            # 端点：只统计请求行（`[INFO]:  URL: `），且只取路径部分。
            #
            # 不要把 `Response URL: ` 也算进来。引擎对同一次请求会先记请求
            # URL、再记响应 URL，两边都数会让每个端点恰好翻倍（实测
            # aweme/post 906 次被记成 1,812）。响应侧的健康度看
            # `Response Code:` 就够了，不需要再数一遍 URL。
            if "INFO]:  URL: " in line:
                for endpoint in ENDPOINTS:
                    if endpoint in line:
                        stats["endpoint_urls"][endpoint] += 1
                        break

            if any(marker in line for marker in FIELD_PRESENCE_LINES):
                for field in FIELD_PRESENCE:
                    if field in line:
                        stats["field_presence"][field] += 1

            for field, pattern in LEAK_PATTERNS.items():
                if pattern.search(line):
                    stats["leak_check"][field] += 1

            for name, needle in KEYWORDS.items():
                if needle in line:
                    stats["keywords"][name] += 1

    return stats


def render(stats: dict) -> str:
    out = []
    add = out.append

    add(f"file            {stats['file']}")
    add(f"size            {stats['size_bytes']:,} bytes")
    add(f"lines           {stats['lines']:,}")
    add(f"window          {stats['first_ts']}  ->  {stats['last_ts']}")
    add(f"active_minutes  {len(stats['minutes'])}")
    add("")

    add("[log levels]")
    for level, count in stats["levels"].most_common():
        add(f"  {level:<10} {count:,}")
    add("")

    add("[http status] (from 'Response Code:' lines)")
    total = sum(stats["http_codes"].values())
    for code, count in sorted(stats["http_codes"].items()):
        share = count / total * 100 if total else 0
        add(f"  {code}        {count:>7,}   {share:6.2f}%")
    add(f"  {'TOTAL':<9} {total:>7,}")

    # 403 比率把两条路径都算进来：正常响应行 + 异常路径。只数前者会漏掉
    # 走 HTTPStatusError 的那些请求。
    abnormal = stats["abnormal_codes"]
    grand_total = total + sum(abnormal.values())
    if grand_total:
        bad = stats["http_codes"].get("403", 0) + abnormal.get("403", 0)
        add(f"  403 rate  {bad / grand_total * 100:.4f}%   ({bad} / {grand_total:,})")
    add("")

    add("[abnormal-path status] (from '响应码异常' lines, NOT in the table above)")
    if abnormal:
        for code, count in sorted(abnormal.items()):
            add(f"  {code}        {count:>7,}")
    else:
        add("  (none)")
    add("")

    add("[endpoints seen] (path only, query discarded)")
    for endpoint, count in stats["endpoint_urls"].most_common():
        add(f"  {count:>7,}  {endpoint}")
    add("")

    add("[signature / param field PRESENCE] (counts only, values never read)")
    for field in FIELD_PRESENCE:
        add(f"  {field:<26} {stats['field_presence'].get(field, 0):,}")
    add("")

    add("[business keywords]")
    for name, count in stats["keywords"].most_common():
        add(f"  {name:<20} {count:,}")
    add("")

    add("[LEAK SELF-CHECK] all values below MUST be 0")
    leaked = False
    for field in MUST_BE_ABSENT:
        count = stats["leak_check"].get(field, 0)
        flag = "  <-- REVIEW" if count else ""
        if count:
            leaked = True
        add(f"  {field:<24} {count}{flag}")
    add("")
    add("RESULT: " + ("REVIEW NEEDED" if leaked else "CLEAN"))
    add("")
    add("NOTE: this report contains no cookie, token, account id, work id,")
    add("      nickname, local path, or URL query string.")

    return "\n".join(out)


def collect(patterns: list[str]) -> list[Path]:
    """接受文件、目录或通配符；目录会递归找 *.log。"""
    found: list[Path] = []
    for pattern in patterns:
        candidate = Path(pattern)
        if candidate.is_file():
            found.append(candidate)
        elif candidate.is_dir():
            found.extend(sorted(candidate.rglob("*.log")))
        else:
            # 通配符：支持绝对路径与 ** 递归
            anchor = candidate.anchor
            if anchor:
                relative = str(candidate.relative_to(anchor))
                found.extend(sorted(Path(anchor).glob(relative)))
            else:
                found.extend(sorted(Path().glob(pattern)))
    # 去重并只留文件
    unique: list[Path] = []
    seen = set()
    for path in found:
        if not path.is_file():
            continue
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def main() -> int:
    targets = collect(argv[1:])

    if not targets:
        _say("usage: python sanitize_log.py <log file | directory | glob> ...")
        _say("")
        _say('  single :  python sanitize_log.py "2026-09-05 10.00.00.log"')
        _say('  folder :  python sanitize_log.py "<engine>/_internal/Volume/Log"')
        _say('  glob   :  python sanitize_log.py "Log/*.log"')
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    everything = []
    for path in targets:
        stats = analyse(path)
        report = render(stats)
        destination = OUTPUT_DIR / f"{path.name}.report.txt"
        destination.write_text(report, encoding="utf-8")
        _say(f"wrote {destination}")
        everything.append(report)

    if len(everything) > 1:
        combined = ("\n" + "=" * 66 + "\n").join(everything)
        (OUTPUT_DIR / "_AGGREGATE.txt").write_text(combined, encoding="utf-8")
        _say(f"wrote {OUTPUT_DIR / '_AGGREGATE.txt'}")

    _say("")
    _say(f"{len(targets)} file(s) processed -> {OUTPUT_DIR}/")
    _say("Check the LEAK SELF-CHECK section reads CLEAN before sharing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
