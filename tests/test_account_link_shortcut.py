"""账号主页长链接本地解析短路的行为测试。

覆盖范围：
* 标准长链接命中本地解析时不发出链接解析 GET；
* 短链接、未知链接、畸形链接、混合文本回退到原有 GET 路径；
* 非 ``type_="user"`` 入口行为不变；
* 回退时仅进入原路径一次，不产生重复请求或重复结果。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.link.extractor import Extractor, ExtractorTikTok

LONG_LINK = (
    "https://www.douyin.com/user/"
    "MS4wLjABAAAAxQ1p8mBvR7nK3sYtL9wZcDfGhJkMnPqRsTuVwXyZaBcDeFgHiJkLmNoPqRsTuVwX"
)
SHORT_LINK = "https://v.douyin.com/4pXudktVCRU/"
MONITOR_TEXT = (
    "0.25 C@u.fo 09/22 :1pm CuS:/ 红色～  "
    "https://v.douyin.com/4pXudktVCRU/ "
    "复制此链接，打开Dou音搜索，直接观看视频！"
)


class FakeRequester:
    """记录 GET 调用次数的替身，返回值模拟跳转后的最终链接。"""

    def __init__(self, resolved: str | None = None) -> None:
        self.calls: list[str] = []
        self.html_calls: list[str] = []
        self.resolved = resolved

    async def run(self, text: str, proxy: str = None) -> str:
        self.calls.append(text)
        return self.resolved if self.resolved is not None else text

    async def request_url(self, url: str, content: str = "url", proxy: str = None):
        self.html_calls.append(url)
        return ""


def build_extractor(resolved: str | None = None) -> tuple[Extractor, FakeRequester]:
    """构造只替换 requester 的 Extractor，避免依赖 Parameter 与网络。"""
    extractor = Extractor.__new__(Extractor)
    requester = FakeRequester(resolved)
    extractor.requester = requester
    return extractor, requester


class AccountShortcutHitTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_long_link_skips_get(self) -> None:
        extractor, requester = build_extractor()
        result = await extractor.run(LONG_LINK, "user")

        self.assertEqual(result, [LONG_LINK.rsplit("/", 1)[-1]])
        self.assertEqual(requester.calls, [], "命中长链接时不应发出链接解析 GET")

    async def test_result_matches_original_get_path(self) -> None:
        """短路结果必须与原 GET 路径的结果逐项一致。"""
        shortcut, shortcut_requester = build_extractor()
        baseline, baseline_requester = build_extractor()

        fast = await shortcut.run(LONG_LINK, "user")
        slow = baseline.user(await baseline_requester.run(LONG_LINK))

        self.assertEqual(fast, slow)
        self.assertEqual(shortcut_requester.calls, [])
        self.assertEqual(len(baseline_requester.calls), 1)

    async def test_query_string_is_parsed_locally(self) -> None:
        extractor, requester = build_extractor()
        url = f"{LONG_LINK}?from_tab_name=main&vid=7412345678901234567"
        result = await extractor.run(url, "user")

        self.assertEqual(result, [LONG_LINK.rsplit("/", 1)[-1]])
        self.assertEqual(requester.calls, [])

    async def test_multiple_long_links_all_skip_get(self) -> None:
        second = LONG_LINK[:-4] + "WxYz"
        extractor, requester = build_extractor()
        result = await extractor.run(f"{LONG_LINK} {second}", "user")

        self.assertEqual(
            result,
            [LONG_LINK.rsplit("/", 1)[-1], second.rsplit("/", 1)[-1]],
        )
        self.assertEqual(requester.calls, [])


class AccountShortcutFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_link_uses_original_get(self) -> None:
        extractor, requester = build_extractor(resolved=LONG_LINK)
        result = await extractor.run(SHORT_LINK, "user")

        self.assertEqual(result, [LONG_LINK.rsplit("/", 1)[-1]])
        self.assertEqual(requester.calls, [SHORT_LINK], "短链接必须走原 GET 路径")

    async def test_monitor_text_short_link_uses_original_get(self) -> None:
        extractor, requester = build_extractor(resolved=LONG_LINK)
        result = await extractor.run(MONITOR_TEXT, "user")

        self.assertEqual(result, [LONG_LINK.rsplit("/", 1)[-1]])
        self.assertEqual(requester.calls, [MONITOR_TEXT])

    async def test_mixed_text_falls_back_for_every_url(self) -> None:
        """混合文本中存在短链接时，长链接也不得跳过 GET。"""
        extractor, requester = build_extractor(resolved=LONG_LINK)
        result = await extractor.run(f"{LONG_LINK} {SHORT_LINK}", "user")

        self.assertEqual(len(requester.calls), 1, "回退只能进入原路径一次")
        self.assertEqual(result, [LONG_LINK.rsplit("/", 1)[-1]])

    async def test_unknown_and_malformed_links_fall_back_once(self) -> None:
        for text in (
            "https://www.douyin.com/video/7412345678901234567",
            "https://www.douyin.com/user/",
            "https://www.douyin.com/user/短",
            "https://www.douyin.com/user/abc",  # 长度不足
            "https://www.douyin.com/user/a/b",  # 路径段过多
            "http://www.douyin.com/user/" + "M" * 40,  # 非 https
            "https://www.iesdouyin.com/share/user/MS4wLjABAAAA?sec_uid=x",
            "not a url at all",
        ):
            with self.subTest(text=text):
                extractor, requester = build_extractor(resolved=LONG_LINK)
                await extractor.run(text, "user")
                self.assertEqual(
                    len(requester.calls), 1, "回退必须且只能进入原 GET 路径一次"
                )

    async def test_empty_text_falls_back(self) -> None:
        extractor, requester = build_extractor(resolved="")
        result = await extractor.run("", "user")

        self.assertEqual(result, [])
        self.assertEqual(len(requester.calls), 1)

    async def test_shortcut_exception_does_not_break_fallback(self) -> None:
        """短路内部异常必须被吞掉并回退，不能中断原 GET 兜底。"""
        extractor, requester = build_extractor(resolved=LONG_LINK)

        def boom(_url):
            raise RuntimeError("validation failure")

        with patch.object(Extractor, "_valid_account_url", staticmethod(boom)):
            result = await extractor.run(LONG_LINK, "user")

        self.assertEqual(len(requester.calls), 1, "异常后必须回退到原 GET 路径")
        self.assertEqual(result, [LONG_LINK.rsplit("/", 1)[-1]])

    async def test_shortcut_returning_mismatch_falls_back(self) -> None:
        """本地校验与 user() 结果不一致时必须回退，避免语义分叉。"""
        extractor, requester = build_extractor(resolved=LONG_LINK)

        with patch.object(Extractor, "user", lambda _self, _urls: ["DIFFERENT"]):
            await extractor.run(LONG_LINK, "user")

        self.assertEqual(len(requester.calls), 1)


class OtherEntryPointsUnchangedTests(unittest.IsolatedAsyncioTestCase):
    async def test_detail_entry_still_requests(self) -> None:
        detail = "https://www.douyin.com/video/7412345678901234567"
        extractor, requester = build_extractor(resolved=detail)
        result = await extractor.run(detail, "detail")

        self.assertEqual(requester.calls, [detail])
        self.assertEqual(result, ["7412345678901234567"])

    async def test_mix_entry_still_requests(self) -> None:
        mix = "https://www.douyin.com/collection/7412345678901234567"
        extractor, requester = build_extractor(resolved=mix)
        await extractor.run(mix, "mix")

        self.assertEqual(requester.calls, [mix])

    async def test_raw_entry_still_requests(self) -> None:
        extractor, requester = build_extractor(resolved=LONG_LINK)
        result = await extractor.run(LONG_LINK, "")

        self.assertEqual(requester.calls, [LONG_LINK])
        self.assertEqual(result, LONG_LINK)

    async def test_account_long_link_under_detail_entry_is_untouched(self) -> None:
        """账号长链接走 detail 入口时不得进入新分支。"""
        extractor, requester = build_extractor(resolved=LONG_LINK)
        await extractor.run(LONG_LINK, "detail")

        self.assertEqual(requester.calls, [LONG_LINK])

    async def test_tiktok_extractor_never_uses_shortcut(self) -> None:
        """TikTok 账号入口仍走 requester.run + get_html_data，不进入新分支。"""
        extractor = ExtractorTikTok.__new__(ExtractorTikTok)
        requester = FakeRequester(resolved="https://www.tiktok.com/@someone")
        extractor.requester = requester
        await extractor.run("https://www.tiktok.com/@someone", "user")

        self.assertEqual(len(requester.calls), 1, "TikTok 入口必须完全保持原有行为")
        self.assertEqual(
            requester.html_calls,
            ["https://www.tiktok.com/@someone"],
            "TikTok 仍需请求页面提取 secUid",
        )

    async def test_douyin_shortcut_is_name_mangled_private(self) -> None:
        """短路方法为 Extractor 私有，子类无法意外复用。"""
        self.assertTrue(hasattr(Extractor, "_Extractor__account_shortcut"))
        self.assertFalse(hasattr(ExtractorTikTok, "_ExtractorTikTok__account_shortcut"))


class MonitorModeUnchangedTests(unittest.IsolatedAsyncioTestCase):
    """后台监听模式调用 run(url) 不带 type_，默认 detail，不进入新分支。"""

    async def test_default_type_is_detail_not_user(self) -> None:
        import inspect

        signature = inspect.signature(Extractor.run)
        self.assertEqual(signature.parameters["type_"].default, "detail")

    async def test_monitor_style_call_always_requests(self) -> None:
        """监听模式即使收到账号长链接也必须走原 GET 路径。"""
        for text in (LONG_LINK, SHORT_LINK, MONITOR_TEXT):
            with self.subTest(text=text[:40]):
                extractor, requester = build_extractor(resolved=text)
                await extractor.run(text)  # 与 main_monitor.py 一致，不传 type_
                self.assertEqual(
                    len(requester.calls), 1, "监听模式行为必须完全不变"
                )

    async def test_monitor_short_link_still_resolves_detail(self) -> None:
        detail = "https://www.douyin.com/video/7412345678901234567"
        extractor, requester = build_extractor(resolved=detail)
        result = await extractor.run(MONITOR_TEXT)

        self.assertEqual(requester.calls, [MONITOR_TEXT])
        self.assertEqual(result, ["7412345678901234567"])


class KnownDefectNotWorsenedTests(unittest.IsolatedAsyncioTestCase):
    """原作者已有缺陷（分页异常/重试耗尽可能被判为完成）本次不修复。

    此处仅确认短路不会新增一条把失败伪报为成功的路径：
    短路只在本地校验完全通过时返回结果，任何异常都回退，
    不会把 GET 失败转换成"成功但空结果"。
    """

    async def test_shortcut_never_masks_a_failed_get(self) -> None:
        class FailingRequester(FakeRequester):
            async def run(self, text: str, proxy: str = None) -> str:
                self.calls.append(text)
                raise RuntimeError("network down")

        extractor = Extractor.__new__(Extractor)
        requester = FailingRequester()
        extractor.requester = requester

        # 短链接必须回退，且 GET 的异常必须向上抛出，不能被短路吞成空结果
        with self.assertRaises(RuntimeError):
            await extractor.run(SHORT_LINK, "user")
        self.assertEqual(len(requester.calls), 1)

    async def test_shortcut_miss_returns_empty_not_success(self) -> None:
        """本地校验失败时返回空列表，由原路径决定成败，不自行判定成功。"""
        self.assertEqual(Extractor._valid_account_url(SHORT_LINK), "")
        self.assertEqual(Extractor._valid_account_url("garbage"), "")


class ValidationRuleTests(unittest.TestCase):
    def test_valid_account_url_accepts_real_shapes(self) -> None:
        self.assertTrue(Extractor._valid_account_url(LONG_LINK))
        self.assertTrue(Extractor._valid_account_url(f"{LONG_LINK}?from=x"))

    def test_valid_account_url_rejects_everything_else(self) -> None:
        for url in (
            SHORT_LINK,
            "https://www.douyin.com/user/",
            "https://www.douyin.com/user/tooshort",
            "https://www.douyin.com/video/7412345678901234567",
            "https://m.douyin.com/user/" + "M" * 40,
            "https://www.douyin.com.evil.com/user/" + "M" * 40,
            "https://www.douyin.com/user/" + "M" * 40 + "/extra",
        ):
            with self.subTest(url=url):
                self.assertEqual(Extractor._valid_account_url(url), "")


if __name__ == "__main__":
    unittest.main()
