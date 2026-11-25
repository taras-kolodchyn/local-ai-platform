"""Exercise scheduled admission against the controller's real SQLite store."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from moyai.contracts import NewMessage, NewSession
from moyai.models import Session, Turn, User
from moyai.runtime import Runtime
from moyai.settings import Settings
from moyai.settings_models import SettingAutomation, SettingAutomationRun
from moyai.settings_runtime import admit_automation
from pydantic_ai.models.test import TestModel
from sqlalchemy import select


class ScheduledTurnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = Settings(data_dir=Path(tmp.name), _env_file=None,
                            workspace_password='ephemeral-test-password', default_model='test')
        self.runtime = Runtime(settings)
        await self.runtime.start(models={'test': TestModel()})
        self.addAsyncCleanup(self.runtime.close)
        self.database = self.runtime.database
        async with self.database.sessions() as db:
            self.actor = await db.scalar(select(User).where(User.username == 'admin'))
        self.chat = await self.database.create_session(self.actor, NewSession(visibility='private'))
        self.schedule = await self.create_schedule('First schedule')

    async def create_schedule(self, name):
        async with self.database.sessions.begin() as db:
            schedule = SettingAutomation(
                name=name, prompt=self.runtime.security.seal('Check the queue'),
                owner_id=self.actor.id, owner_auth_version=self.actor.auth_version,
                session_id=self.chat.id, interval_minutes=5, model='test', state='ready',
            )
            db.add(schedule)
            await db.flush()
            return schedule.id

    async def set_turn_status(self, turn_id, status):
        async with self.database.sessions.begin() as db:
            chat = await db.get(Session, self.chat.id)
            turn = await db.get(Turn, turn_id)
            turn.status = status
            chat.active_turn = turn_id if status in {'running', 'waiting'} else None
            chat.status = status if chat.active_turn else 'idle'

    async def test_skips_while_busy_and_recovers_when_idle(self):
        first = await admit_automation(self.runtime, self.schedule, 'initial')
        for status in ('queued', 'running', 'waiting'):
            with self.subTest(status=status):
                await self.set_turn_status(first['turn_id'], status)
                result = await admit_automation(self.runtime, self.schedule, status)
                self.assertEqual(result, {'status': 'skipped'})
        await self.set_turn_status(first['turn_id'], 'completed')
        self.assertEqual(await admit_automation(self.runtime, self.schedule, 'waiting'), {'status': 'skipped'})
        self.assertEqual(await admit_automation(self.runtime, self.schedule, 'initial'), first)
        next_run = await admit_automation(self.runtime, self.schedule, 'idle')
        self.assertEqual(next_run['status'], 'admitted')
        self.assertNotEqual(next_run['turn_id'], first['turn_id'])

    async def test_two_schedules_on_one_chat_admit_only_one_turn(self):
        second = await self.create_schedule('Second schedule')
        one, two = await asyncio.gather(
            admit_automation(self.runtime, self.schedule, 'same-minute'),
            admit_automation(self.runtime, second, 'same-minute'),
        )
        self.assertCountEqual([one['status'], two['status']], ['admitted', 'skipped'])

    async def test_existing_turn_recovers_and_manual_input_can_still_queue(self):
        first = await admit_automation(self.runtime, self.schedule, 'first')
        async with self.database.sessions.begin() as db:
            receipt = await db.scalar(select(SettingAutomationRun).where(SettingAutomationRun.automation_id == self.schedule))
            receipt.turn_id = None
        self.assertEqual(await admit_automation(self.runtime, self.schedule, 'first'), first)
        manual = await self.database.submit(self.actor, self.chat.id, NewMessage(prompt='Manual', request_key='manual'))
        self.assertEqual(manual.status, 'queued')
