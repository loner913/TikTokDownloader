"""抖音加密参数生成代码（外挂实现）。

用途
----
上游 DouK-Downloader 自 5.8 起将内置 a_bogus 算法置为占位常量（提交
ef77a70，2026-08-21），签名能力改由项目根目录的 encipher.py 外挂提供。
本文件实现该扩展点：调用抖音官方 WebSecSDK（static/js/webmssdk.es5.js）
在 Node 沙箱内生成 x-secsdk-web-signature。

依赖
----
- Node.js >= 18（本机已验证 v24.18.0）
- Python 包 javascript（JSPyBridge）
- static/js/ 下四个 JS 文件：
    environment.js / runtime_bundler_34.js / webmssdk.es5.js / sdk-glue.js

来源声明
--------
JS 文件取自 https://github.com/kamiertop/videodown
Python 结构参考 https://github.com/JoeanAmier/TikTokDownloader (develop)
及 https://github.com/mlkt/TikTokDownloader 的 encipher.py

放置位置
--------
与主程序可执行文件同级目录（打包版），或项目根目录（源码运行）。
"""

from json import dumps, loads
from pathlib import Path
from shutil import which
from subprocess import PIPE, run
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit

__all__ = ["DouYinParams"]

USERAGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

# 与 API.params 中公开声明的浏览器几何保持一致，避免签名内部信息与
# 查询参数自相矛盾。
SCREEN_WIDTH = 1536
SCREEN_HEIGHT = 864


def _root() -> Path:
    """定位 static/js 所在目录，兼容 PyInstaller / cx_Freeze 冻结产物。"""
    import sys

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for candidate in (exe_dir, exe_dir / "_internal"):
            if (candidate / "static" / "js" / "webmssdk.es5.js").is_file():
                return candidate
        return exe_dir
    return Path(__file__).resolve().parent


ROOT = _root()

JS_FILES = (
    "environment.js",
    "runtime_bundler_34.js",
    "webmssdk.es5.js",
    "sdk-glue.js",
)


def _node_available(min_version: int = 18) -> bool:
    node = which("node")
    if node is None:
        return False
    try:
        completed = run(
            [node, "--version"],
            stdout=PIPE,
            stderr=PIPE,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            return False
        major = int(completed.stdout.strip().removeprefix("v").split(".", 1)[0])
        return major >= min_version
    except Exception:
        return False


def _query_to_string(query) -> str:
    if query is None:
        return ""
    if isinstance(query, str):
        return query
    if isinstance(query, dict):
        return urlencode(query, doseq=True, safe="=")
    raise TypeError(f"query 类型错误: {type(query)!r}")


def _query_value(query: str, name: str) -> str:
    return next(
        (
            value
            for key, value in parse_qsl(query, keep_blank_values=True)
            if key == name
        ),
        "",
    )


class _WebSign:
    """在 Node 沙箱内执行抖音 WebSecSDK。

    JSPyBridge 的 require 只在首次调用时付出启动成本（实测 0.67s），
    之后每次签名约 1ms。桥文件按 UA 内容寻址，UA 变化会重建。
    """

    def __init__(self, user_agent: str = USERAGENT):
        self._user_agent = user_agent
        self._module = None
        self._lock = Lock()
        self._failed = False

    def _bridge_source(self) -> str:
        files = ", ".join(
            dumps(str(ROOT.joinpath("static", "js", name).resolve()))
            for name in JS_FILES
        )
        return f"""
const fs = require("fs");
const vm = require("vm");

const context = vm.createContext({{
    console, Buffer, URL, URLSearchParams, TextEncoder, TextDecoder,
    setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: {{
        userAgent: {dumps(self._user_agent)},
        language: "zh-CN",
        platform: "Win32"
    }},
    location: {{ href: "https://www.douyin.com/" }}
}});

context.globalThis = context;
context.window = context;
context.self = context;
context.global = context;

context.atob = function (value) {{
    return Buffer.from(String(value), "base64").toString("utf8");
}};
context.btoa = function (value) {{
    return Buffer.from(String(value), "utf8").toString("base64");
}};
context.__unixMillis = function () {{ return Date.now(); }};

for (const file of [{files}]) {{
    vm.runInContext(fs.readFileSync(file, "utf8"), context, {{ filename: file }});
}}

vm.runInContext(
`window._SdkGlueInit(
    {{
        self: {{ aid: 6383, pageId: 6241 }},
        bdms: {{
            aid: 6383,
            pageId: 6241,
            paths: ["^/aweme/v1/", "^/aweme/v2/"],
            boe: false,
            ddrt: 8.5,
            ic: 8.5
        }}
    }},
    {{}}
);`,
    context
);

module.exports = {{
    sign: function (targetURL, uifid) {{
        context.__uifid = String(uifid);
        const fn = vm.runInContext(`window.use("webSignUrl")`, context);
        if (typeof fn !== "function") {{
            throw new Error("window.use('webSignUrl') 未返回函数");
        }}
        const result = fn(String(targetURL));
        if (!result) throw new Error("webSignUrl 返回为空");
        const url = result.url || "";
        const headers = result.headers || {{}};
        const signature = headers["x-secsdk-web-signature"] || "";
        if (!url) throw new Error("webSignUrl 未返回 URL");
        return JSON.stringify({{ url, signature }});
    }}
}};
"""

    def _ensure_module(self):
        if self._module is not None or self._failed:
            return self._module
        with self._lock:
            if self._module is not None or self._failed:
                return self._module
            try:
                bridge = ROOT.joinpath("static", "js", "douyin_websign.cjs")
                bridge.parent.mkdir(parents=True, exist_ok=True)
                bridge.write_text(self._bridge_source(), encoding="utf-8")
                from javascript import require

                self._module = require(str(bridge.resolve()))
            except Exception:
                # 签名不可用时不应让整个采集流程崩溃；退化为不加签名，
                # 由上层的响应码处理逻辑接管。
                self._failed = True
                self._module = None
        return self._module

    def sign(self, url: str, uifid: str) -> tuple[str, str]:
        module = self._ensure_module()
        if module is None:
            return "", ""
        try:
            with self._lock:
                raw = module.sign(url, uifid)
        except Exception:
            return "", ""
        try:
            result = loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            return "", ""
        return str(result.get("url", "")), str(result.get("signature", ""))


try:  # 上游 5.8+ 提供 Params 抽象基类；缺失时退化为普通对象。
    from src.encrypt.params import Params as _Base
except Exception:  # pragma: no cover

    class _Base:  # type: ignore[no-redef]
        def __init__(self) -> None:
            pass


class DouYinParams(_Base):
    """抖音加密参数实现。

    sign()      -> {"a_bogus": ...}         仅本地算法
    sign_url()  -> 带签名的 query 字符串     WebSign（需要 uifid）

    上游按 sign_url() 的返回值直接拼 URL，因此这里返回 query 而非完整
    URL；传入 url 为空时行为与旧实现一致。
    """

    def __init__(self) -> None:
        super().__init__()
        self._ab = None
        self._websign = _WebSign() if _node_available() else None

    # ------------------------------------------------------------------
    # a_bogus：保留本地算法，作为不需要 WebSign 的接口的兜底
    # ------------------------------------------------------------------

    def _a_bogus(self, query: str, data, method: str, user_agent: str) -> str:
        if self._ab is None:
            try:
                from src.encrypt.aBogus import ABogus

                try:
                    self._ab = ABogus(
                        user_agent,
                        "Win32",
                        SCREEN_WIDTH,
                        SCREEN_HEIGHT,
                    )
                except TypeError:
                    # 旧版 ABogus 不接受屏幕几何参数。
                    self._ab = ABogus(user_agent)
            except Exception:
                self._ab = False
        if not self._ab:
            return ""
        try:
            try:
                return self._ab.get_value(
                    query, data, method, user_agent=user_agent
                )
            except TypeError:
                # 你的基线签名为 get_value(url_params, method)。
                return self._ab.get_value(query, method)
        except Exception:
            return ""

    def sign(
        self,
        url: str = "",
        query=None,
        data=None,
        method: str = "",
        user_agent: str = USERAGENT,
        ms_token: str = "",
    ) -> dict:
        return {
            "a_bogus": self._a_bogus(
                _query_to_string(query), data, method, user_agent
            )
        }

    def sign_url(
        self,
        url: str = "",
        query=None,
        data=None,
        method: str = "",
        user_agent: str = USERAGENT,
        ms_token: str = "",
    ) -> str:
        query = _query_to_string(query)

        items = [
            (key, value)
            for key, value in parse_qsl(query, keep_blank_values=True)
            if key != "a_bogus"
        ]
        if a_bogus := self._a_bogus(query, data, method, user_agent):
            items.append(("a_bogus", a_bogus))
        signed_query = urlencode(items, doseq=True)

        if not url or self._websign is None:
            return signed_query

        uifid = _query_value(signed_query, "uifid")
        if not uifid:
            # 并非所有接口都要求 WebSign；无 uifid 时保持旧行为。
            return signed_query

        existing = urlsplit(url).query
        full_query = f"{existing}&{signed_query}" if existing else signed_query
        full_url = f"{url.split('?')[0]}?{full_query}"

        signed_url, signature = self._websign.sign(full_url, uifid)
        if not signed_url:
            return signed_query

        final_query = urlsplit(signed_url).query
        if not final_query:
            return signed_query
        if signature and "x-secsdk-web-signature=" not in final_query:
            final_query += f"&x-secsdk-web-signature={signature}"
        return final_query
