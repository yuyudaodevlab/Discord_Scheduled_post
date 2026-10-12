import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from storage import Storage
from utils import UserError, parse_time, split_message


class TimeTests(unittest.TestCase):
    now = datetime(2026, 10, 13, tzinfo=UTC)

    def test_absolute(self):
        for value in ("2026-10-30 21:00", "2026/10/30 21:00"):
            self.assertEqual(parse_time(value, now=self.now), datetime(2026, 10, 30, 12, tzinfo=UTC))
        self.assertEqual(parse_time("2026-10-30 21:00:30", now=self.now).second, 30)

    def test_relative(self):
        for japanese, short, seconds in (("30秒後", "30s", 30), ("10分後", "10m", 600),
                                         ("2時間後", "2h", 7200), ("3日後", "3d", 259200)):
            for value in (japanese, short):
                with self.subTest(value=value):
                    self.assertEqual(parse_time(value, now=self.now), self.now + timedelta(seconds=seconds))

    def test_invalid(self):
        for value in ("2026-02-30 21:00", "明日", "-1m", "1.5h", "2026-10/30 21:00"):
            with self.subTest(value=value), self.assertRaises(UserError):
                parse_time(value, now=self.now)

    def test_past(self):
        for value in ("2020-01-01 00:00", "0s"):
            with self.assertRaisesRegex(UserError, "すでに過ぎ"):
                parse_time(value, now=self.now)

    def test_dst(self):
        for value in ("2027-03-14 02:30", "2026-11-01 01:30"):
            with self.assertRaises(UserError):
                parse_time(value, "America/New_York", now=self.now)

    def test_splitting_preserves_content(self):
        content = "😀" * 1200 + "\n\n```python\n" + "あ" * 2200 + "\n```<@123>||秘密||"
        parts = split_message(content)
        self.assertEqual("".join(parts), content)
        self.assertTrue(all(len(p.encode("utf-16-le")) // 2 <= 2000 for p in parts))


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.json"
        self.store = Storage(self.path)
        self.store.load()

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_persistence_and_backup(self):
        await self.store.change(lambda d: d["guilds"].update({"123": {"timezone": "Asia/Tokyo"}}))
        await self.store.change(lambda d: d["guilds"].update({"456": {"timezone": "UTC"}}))
        self.store.close()
        self.store = Storage(self.path)
        self.store.load()
        self.assertIn("456", self.store.data["guilds"])
        self.store.close()
        self.path.write_text("broken", encoding="utf-8")
        self.store = Storage(self.path)
        with self.assertLogs("storage", level="WARNING"):
            self.store.load()
        self.assertIn("123", self.store.data["guilds"])
        self.assertNotIn("456", self.store.data["guilds"])

    async def test_concurrent_updates(self):
        import asyncio
        await asyncio.gather(*(self.store.change(lambda d, i=i: d["users"].update({str(i): {"notifications": [10]}})) for i in range(10)))
        self.assertEqual(len(self.store.data["users"]), 10)

    async def test_rejected_transaction_does_not_change_memory(self):
        def invalid(data):
            data["guilds"]["bad"] = {"timezone": "UTC"}
            raise UserError("拒否")
        with self.assertRaises(UserError):
            await self.store.change(invalid)
        self.assertNotIn("bad", self.store.data["guilds"])


if __name__ == "__main__":
    unittest.main()
