import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs

from httpx import AsyncClient, MockTransport, Response

from src.application.main_terminal import TikTok
from src.interface.info import Info


def _info(sec_uid: str, number: int | str = 1) -> dict:
    return {
        "sec_uid": sec_uid,
        "nickname": f"name-{number}",
        "uid": f"uid-{number}",
    }


class InfoBatchMappingTests(unittest.IsolatedAsyncioTestCase):
    def make_owner(self):
        owner = TikTok.__new__(TikTok)
        owner.logger = SimpleNamespace(warning=Mock(), info=Mock())
        return owner

    async def test_response_order_maps_by_returned_sec_uid(self):
        owner = self.make_owner()
        owner._get_info_data = AsyncMock(
            return_value=[_info("id-b", "b"), _info("id-a", "a")]
        )

        result = await owner._prefetch_account_info(["id-a", "id-b"])

        self.assertEqual(result["id-a"]["uid"], "uid-a")
        self.assertEqual(result["id-b"]["uid"], "uid-b")
        owner._get_info_data.assert_awaited_once_with(
            sec_user_id=["id-a", "id-b"], first=False
        )

    async def test_integer_uid_is_preserved_as_a_valid_original_record(self):
        owner = self.make_owner()
        record = {"sec_uid": "id-a", "nickname": "name-a", "uid": 12345}
        owner._get_info_data = AsyncMock(return_value=[record])

        result = await owner._prefetch_account_info(["id-a"])

        self.assertIs(result["id-a"], record)

    async def test_missing_extra_duplicate_and_malformed_records_fallback(self):
        owner = self.make_owner()
        owner._get_info_data = AsyncMock(
            return_value=[
                _info("id-a", "a"),
                _info("id-a", "duplicate"),
                _info("outside", "extra"),
                {"sec_uid": "id-c", "nickname": "missing-uid"},
                {"sec_uid": ""},
                "not-a-record",
            ]
        )

        result = await owner._prefetch_account_info(["id-a", "id-b", "id-c"])

        self.assertEqual(result, {})

    async def test_batch_exception_falls_back_for_every_requested_id(self):
        owner = self.make_owner()
        owner._get_info_data = AsyncMock(side_effect=RuntimeError("transport"))

        result = await owner._prefetch_account_info(["id-a", "id-b"])

        self.assertEqual(result, {})

    async def test_empty_request_does_not_call_info(self):
        owner = self.make_owner()
        owner._get_info_data = AsyncMock()

        self.assertEqual(await owner._prefetch_account_info([]), {})
        owner._get_info_data.assert_not_awaited()


class ManagerBatchEntryTests(unittest.IsolatedAsyncioTestCase):
    def make_owner(self, count: int):
        owner = TikTok.__new__(TikTok)
        owner.accounts = []
        owner.logger = SimpleNamespace(info=Mock(), warning=Mock())
        owner.console = object()
        owner.INFO_BATCH_SIZE = 5
        owner._TikTok__summarize_results = Mock()
        owner.check_sec_user_id = AsyncMock(
            side_effect=lambda url, tiktok=False: url.removeprefix("url-")
        )
        owner._prefetch_account_info = AsyncMock(
            side_effect=lambda ids: {i: _info(i, i) for i in ids}
        )
        owner.deal_account_detail = AsyncMock(return_value=True)
        accounts = [SimpleNamespace(url=f"url-id-{i}", mark=f"m-{i}") for i in range(count)]
        return owner, accounts

    async def test_tasks_stay_in_order_and_duplicate_ids_are_not_deduplicated(self):
        owner, accounts = self.make_owner(6)
        accounts[1].url = accounts[0].url
        with patch(
            "src.application.main_terminal.get_suspend_options", return_value=(50, 0)
        ), patch("src.application.main_terminal.suspend", new_callable=AsyncMock) as wait:
            await owner._TikTok__account_detail_batch_douyin(accounts, "accounts_urls")

        self.assertEqual(
            [call.args[0] for call in owner.deal_account_detail.await_args_list],
            list(range(1, 7)),
        )
        self.assertEqual(
            [call.kwargs["sec_user_id"] for call in owner.deal_account_detail.await_args_list],
            [f"id-{0}", f"id-{0}", f"id-{2}", f"id-{3}", f"id-{4}", f"id-{5}"],
        )
        self.assertEqual(
            [call.args[0] for call in owner._prefetch_account_info.await_args_list],
            [["id-0", "id-2", "id-3", "id-4"], ["id-5"]],
        )
        self.assertEqual(wait.await_count, 5)

    async def test_batches_stop_at_suspend_boundary(self):
        owner, accounts = self.make_owner(8)
        owner._prefetch_account_info = AsyncMock(
            side_effect=lambda ids: {i: _info(i, i) for i in ids}
        )
        with patch(
            "src.application.main_terminal.get_suspend_options", return_value=(3, 0)
        ), patch("src.application.main_terminal.suspend", new_callable=AsyncMock):
            await owner._TikTok__account_detail_batch_douyin(accounts, "accounts_urls")

        self.assertEqual(
            [call.args[0] for call in owner._prefetch_account_info.await_args_list],
            [["id-0", "id-1", "id-2"], ["id-3", "id-4", "id-5"], ["id-6", "id-7"]],
        )

    async def test_link_parse_failure_is_not_prefetched_and_keeps_position(self):
        owner, accounts = self.make_owner(3)
        owner.check_sec_user_id = AsyncMock(side_effect=["id-0", "", "id-2"])
        with patch(
            "src.application.main_terminal.get_suspend_options", return_value=(50, 0)
        ), patch("src.application.main_terminal.suspend", new_callable=AsyncMock) as wait:
            await owner._TikTok__account_detail_batch_douyin(accounts, "accounts_urls")

        owner._prefetch_account_info.assert_awaited_once_with(["id-0", "id-2"])
        self.assertEqual(
            [call.args[0] for call in owner.deal_account_detail.await_args_list], [1, 3]
        )
        self.assertEqual(wait.await_count, 2)
        warning = owner.logger.warning.call_args.args[0]
        self.assertNotIn("url-id-1", warning)
        self.assertNotIn("m-1", warning)
        self.assertIn("第 2 个账号", warning)

    async def test_5_10_20_offline_sizes_preserve_order_and_request_counts(self):
        for batch_size in (5, 10, 20):
            with self.subTest(batch_size=batch_size):
                owner, accounts = self.make_owner(batch_size + 1)
                owner.INFO_BATCH_SIZE = batch_size
                with patch(
                    "src.application.main_terminal.get_suspend_options",
                    return_value=(50, 0),
                ), patch("src.application.main_terminal.suspend", new_callable=AsyncMock):
                    await owner._TikTok__account_detail_batch_douyin(
                        accounts, "accounts_urls"
                    )

                self.assertEqual(owner._prefetch_account_info.await_count, 2)
                self.assertEqual(
                    [call.args[0] for call in owner.deal_account_detail.await_args_list],
                    list(range(1, batch_size + 2)),
                )

    async def test_missing_prefetch_uses_original_single_account_path(self):
        owner, accounts = self.make_owner(2)
        owner._prefetch_account_info = AsyncMock(return_value={"id-0": _info("id-0", 0)})
        owner.get_user_info_data = AsyncMock(return_value=_info("id-1", 1))
        with patch(
            "src.application.main_terminal.get_suspend_options", return_value=(50, 0)
        ), patch("src.application.main_terminal.suspend", new_callable=AsyncMock):
            await owner._TikTok__account_detail_batch_douyin(accounts, "accounts_urls")

        self.assertIsNotNone(owner.deal_account_detail.await_args_list[0].kwargs["prefetched_info"])
        self.assertIsNone(owner.deal_account_detail.await_args_list[1].kwargs["prefetched_info"])

    async def test_manager_environment_is_required_for_batch_path(self):
        owner, accounts = self.make_owner(1)
        owner._TikTok__account_detail_batch_single = AsyncMock()
        owner._TikTok__account_detail_batch_douyin = AsyncMock()

        with patch("src.application.main_terminal.environ", {}):
            await owner._TikTok__account_detail_batch(accounts, "accounts_urls", False)
        owner._TikTok__account_detail_batch_single.assert_awaited_once_with(
            accounts, "accounts_urls", False
        )
        owner._TikTok__account_detail_batch_douyin.assert_not_awaited()

        owner._TikTok__account_detail_batch_single.reset_mock()
        with patch(
            "src.application.main_terminal.environ",
            {
                "DOUK_MANAGER_BACKUP": "isolated",
                "DOUK_ACCOUNT_BATCH_SIZE": "50",
                "DOUK_ACCOUNT_REST_SECONDS": "30",
            },
        ):
            await owner._TikTok__account_detail_batch(accounts, "accounts_urls", False)
        owner._TikTok__account_detail_batch_douyin.assert_awaited_once_with(
            accounts, "accounts_urls"
        )
        owner._TikTok__account_detail_batch_single.assert_not_awaited()

    async def test_tiktok_batch_keeps_original_path_even_in_manager_environment(self):
        owner, accounts = self.make_owner(1)
        owner._TikTok__account_detail_batch_single = AsyncMock()
        owner._TikTok__account_detail_batch_douyin = AsyncMock()
        environment = {
            "DOUK_MANAGER_BACKUP": "isolated",
            "DOUK_ACCOUNT_BATCH_SIZE": "50",
            "DOUK_ACCOUNT_REST_SECONDS": "30",
        }

        with patch("src.application.main_terminal.environ", environment):
            await owner._TikTok__account_detail_batch(accounts, "accounts_urls_tiktok", True)

        owner._TikTok__account_detail_batch_single.assert_awaited_once_with(
            accounts, "accounts_urls_tiktok", True
        )
        owner._TikTok__account_detail_batch_douyin.assert_not_awaited()

    async def test_partial_batch_only_falls_back_missing_accounts(self):
        owner, accounts = self.make_owner(5)
        del owner._prefetch_account_info
        owner._get_info_data = AsyncMock(
            return_value=[_info("id-0", 0), _info("id-2", 2), _info("id-4", 4)]
        )
        owner.get_user_info_data = AsyncMock(
            side_effect=lambda *args, sec_user_id, **kwargs: _info(sec_user_id, "single")
        )
        handled = []

        async def deal_account_detail(index, sec_user_id, prefetched_info=None, **kwargs):
            if prefetched_info is None:
                await owner.get_user_info_data(sec_user_id=sec_user_id)
            handled.append((index, sec_user_id, prefetched_info))
            return True

        owner.deal_account_detail = deal_account_detail

        with patch(
            "src.application.main_terminal.get_suspend_options", return_value=(50, 0)
        ), patch("src.application.main_terminal.suspend", new_callable=AsyncMock) as wait:
            await owner._TikTok__account_detail_batch_douyin(accounts, "accounts_urls")

        owner._get_info_data.assert_awaited_once_with(
            sec_user_id=["id-0", "id-1", "id-2", "id-3", "id-4"], first=False
        )
        self.assertEqual(
            [call.kwargs["sec_user_id"] for call in owner.get_user_info_data.await_args_list],
            ["id-1", "id-3"],
        )
        self.assertEqual(len(handled), 5)
        self.assertEqual(wait.await_count, 4)


class DealAccountDetailInfoTests(unittest.IsolatedAsyncioTestCase):
    def make_owner(self):
        owner = TikTok.__new__(TikTok)
        owner.logger = SimpleNamespace(info=Mock(), warning=Mock())
        owner._get_account_data = AsyncMock(return_value=([{"id": "work"}], "", ""))
        owner._batch_process_detail = AsyncMock(return_value=True)
        owner.get_user_info_data = AsyncMock(return_value=_info("id-a", "single"))
        return owner

    async def test_none_prefetch_keeps_single_info_fallback(self):
        owner = self.make_owner()

        result = await owner.deal_account_detail(
            1, "id-a", prefetched_info=None
        )

        self.assertTrue(result)
        owner.get_user_info_data.assert_awaited_once()
        self.assertEqual(
            owner._batch_process_detail.await_args.kwargs["info"],
            _info("id-a", "single"),
        )

    async def test_reliable_prefetch_skips_single_info(self):
        owner = self.make_owner()
        prefetched = _info("id-a", "batch")

        result = await owner.deal_account_detail(
            1, "id-a", prefetched_info=prefetched
        )

        self.assertTrue(result)
        owner.get_user_info_data.assert_not_awaited()
        self.assertIs(owner._batch_process_detail.await_args.kwargs["info"], prefetched)

    async def test_prefetch_only_changes_info_source_not_downstream_arguments(self):
        baseline = self.make_owner()
        prefetched = self.make_owner()
        info = _info("id-a", "same")
        baseline.get_user_info_data = AsyncMock(return_value=info)
        kwargs = {
            "mark": "A1-mark",
            "tab": "post",
            "earliest": "2026-01-01",
            "latest": "2026-08-01",
            "pages": 7,
            "cookie": "configured=present",
            "proxy": "http://proxy.invalid",
            "cursor": 123,
            "count": 18,
        }

        await baseline.deal_account_detail(1, "id-a", **kwargs)
        await prefetched.deal_account_detail(1, "id-a", prefetched_info=info, **kwargs)

        self.assertEqual(
            baseline._get_account_data.await_args.kwargs,
            prefetched._get_account_data.await_args.kwargs,
        )
        self.assertEqual(
            baseline._batch_process_detail.await_args.kwargs,
            prefetched._batch_process_detail.await_args.kwargs,
        )


class InfoRequestCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_params(client):
        return SimpleNamespace(
            headers={"Cookie": "configured=present"},
            logger=Mock(),
            ab=SimpleNamespace(get_value=lambda _params, _method: "controlled"),
            console=Mock(),
            max_retry=0,
            timeout=10,
            client=client,
        )

    async def test_batch_run_returns_full_list_with_one_request_and_one_wait(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return Response(200, json={"data": [_info("id-b", "b"), _info("id-a", "a")]})

        async with AsyncClient(transport=MockTransport(handler)) as client:
            info = Info(self.make_params(client), sec_user_id=["id-a", "id-b"])
            with patch("src.interface.template.wait", new_callable=AsyncMock) as wait:
                result = await info.run(first=False)

        self.assertEqual([item["sec_uid"] for item in result], ["id-b", "id-a"])
        self.assertEqual(len(requests), 1)
        form = parse_qs(requests[0].content.decode())
        self.assertEqual(form["sec_user_ids"], ['["id-a","id-b"]'])
        self.assertIn("configured=present", requests[0].headers.get("Cookie", ""))
        wait.assert_awaited_once_with()

    async def test_default_run_still_returns_first_record(self):
        async def handler(_request):
            return Response(200, json={"data": [_info("id-a", "a"), _info("id-b", "b")]})

        async with AsyncClient(transport=MockTransport(handler)) as client:
            info = Info(self.make_params(client), sec_user_id="id-a")
            with patch("src.interface.template.wait", new_callable=AsyncMock):
                result = await info.run()

        self.assertEqual(result["sec_uid"], "id-a")


if __name__ == "__main__":
    unittest.main()
