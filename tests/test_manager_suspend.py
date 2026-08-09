from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from src.custom.function import get_suspend_options, suspend


class RecordingConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, message: str) -> None:
        self.messages.append(message)


class SuspendOptionsTests(unittest.TestCase):
    def test_direct_launch_falls_back_to_50_150(self) -> None:
        self.assertEqual(get_suspend_options({}), (50, 150))

    def test_manager_values_override_defaults(self) -> None:
        self.assertEqual(
            get_suspend_options(
                {
                    "DOUK_ACCOUNT_BATCH_SIZE": "25",
                    "DOUK_ACCOUNT_REST_SECONDS": "90",
                }
            ),
            (25, 90),
        )

    def test_invalid_values_fall_back_safely(self) -> None:
        self.assertEqual(
            get_suspend_options(
                {
                    "DOUK_ACCOUNT_BATCH_SIZE": "0",
                    "DOUK_ACCOUNT_REST_SECONDS": "invalid",
                }
            ),
            (50, 150),
        )

    def test_zero_seconds_is_allowed(self) -> None:
        self.assertEqual(
            get_suspend_options(
                {
                    "DOUK_ACCOUNT_BATCH_SIZE": "50",
                    "DOUK_ACCOUNT_REST_SECONDS": "0",
                }
            ),
            (50, 0),
        )


class SuspendRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_count_uses_manager_values(self) -> None:
        console = RecordingConsole()
        environment = {
            "DOUK_ACCOUNT_BATCH_SIZE": "2",
            "DOUK_ACCOUNT_REST_SECONDS": "7",
        }
        with patch.dict("src.custom.function.environ", environment, clear=True), patch(
            "src.custom.function.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            await suspend(2, console)

        sleep_mock.assert_awaited_once_with(7)
        self.assertEqual(len(console.messages), 1)
        self.assertIn("2", console.messages[0])
        self.assertIn("7", console.messages[0])

    async def test_nonmatching_count_does_not_pause(self) -> None:
        console = RecordingConsole()
        environment = {
            "DOUK_ACCOUNT_BATCH_SIZE": "50",
            "DOUK_ACCOUNT_REST_SECONDS": "150",
        }
        with patch.dict("src.custom.function.environ", environment, clear=True), patch(
            "src.custom.function.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            await suspend(49, console)

        sleep_mock.assert_not_awaited()
        self.assertEqual(console.messages, [])

    async def test_zero_seconds_disables_pause(self) -> None:
        console = RecordingConsole()
        environment = {
            "DOUK_ACCOUNT_BATCH_SIZE": "1",
            "DOUK_ACCOUNT_REST_SECONDS": "0",
        }
        with patch.dict("src.custom.function.environ", environment, clear=True), patch(
            "src.custom.function.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            await suspend(1, console)

        sleep_mock.assert_not_awaited()
        self.assertEqual(console.messages, [])


if __name__ == "__main__":
    unittest.main()
