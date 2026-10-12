import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from scheduler import Scheduler
from storage import Storage
from utils import utcnow


class RecordingScheduler(Scheduler):
    """Discord通信境界だけを置換し、実際の保存・状態遷移を検証する。"""
    def __init__(self, store):
        super().__init__(None, store, 300)
        self.deliveries = []
        self.dms = []

    async def dispatch(self, record):
        assert self.store.data["schedules"][record["id"]]["status"] == "sending"
        self.deliveries.append(record["id"])
        await self.update(record["id"], status="sent", message_ids=["123"])

    async def dm(self, user_id, text):
        self.dms.append(text)
        return True


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Storage(Path(self.temp.name) / "data.json")
        self.store.load()
        self.service = RecordingScheduler(self.store)

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def add(self, seconds, status="scheduled", notifications=None):
        record = dict(id="SCH-TEST1234", guild_id="1", channel_id="2", author_id="3",
                      content="本文", scheduled_at=(utcnow() + timedelta(seconds=seconds)).isoformat(),
                      created_at=utcnow().isoformat(), notifications=notifications or [], notified=[],
                      status=status, revision=0, allow_everyone=False, message_ids=[])
        await self.store.change(lambda d: d["schedules"].update({record["id"]: record}))

    async def test_due_delivered_once(self):
        await self.add(-10)
        await self.service.tick()
        await self.service.tick()
        self.assertEqual(self.service.deliveries, ["SCH-TEST1234"])

    async def test_old_schedule_missed(self):
        await self.add(-301)
        await self.service.tick()
        self.assertEqual(self.store.data["schedules"]["SCH-TEST1234"]["status"], "missed")
        self.assertFalse(self.service.deliveries)
        self.assertEqual(len(self.service.dms), 1)

    async def test_restart_does_not_resend_uncertain_delivery(self):
        await self.add(-10, "sending")
        await self.service.recover()
        await self.service.tick()
        self.assertEqual(self.store.data["schedules"]["SCH-TEST1234"]["status"], "failed")
        self.assertFalse(self.service.deliveries)

    async def test_notification_persisted_across_restart(self):
        await self.add(120, notifications=[10])
        await self.service.tick()
        self.assertEqual(len(self.service.dms), 1)
        self.store.close()
        self.store = Storage(self.store.path)
        self.store.load()
        self.service = RecordingScheduler(self.store)
        await self.service.tick()
        self.assertFalse(self.service.dms)

    async def test_backup_quarantines_pending_schedules(self):
        await self.add(-10)
        await self.service.update("SCH-TEST1234", status="sending")
        self.store.close()
        self.store.path.write_text("corrupt", encoding="utf-8")
        self.store = Storage(self.store.path)
        with self.assertLogs("storage", level="WARNING"):
            self.store.load()
        self.service = RecordingScheduler(self.store)
        await self.service.tick()
        self.assertFalse(self.service.deliveries)
        self.assertEqual(self.store.data["schedules"]["SCH-TEST1234"]["status"], "failed")

    async def test_command_registration(self):
        from bot import ScheduleBot
        bot = ScheduleBot()
        commands = bot.tree.get_commands()
        self.assertEqual(len(commands), 8)
        group = bot.tree.get_command("schedule-notification")
        self.assertEqual({c.name for c in group.commands}, {"add", "remove", "list", "clear", "test"})
        for command in commands:
            payload = command.to_dict(bot.tree)
            self.assertTrue(payload["name"].startswith("schedule-"))
        await bot.close()
