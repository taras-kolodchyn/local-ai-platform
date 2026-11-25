"""Skip interval occurrences while their conversation has work in progress."""
from importlib.util import find_spec
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update

from moyai.models import Turn
from moyai.settings_models import SettingAutomationRun


class ConversationBusy(Exception):
    """The scheduled occurrence found active or queued work."""


async def check_scheduled_turn(transaction, session):
    # Called under the same conversation lock used by submit and dispatch.
    if not transaction.info.get('require_idle_conversation'):
        return
    if session.active_turn or await transaction.scalar(select(Turn.id).where(
            Turn.session_id == session.id, Turn.status == 'queued').limit(1)):
        raise ConversationBusy


async def submit_scheduled_turn(database, owner, session_id, message, receipt_id):
    async with database.sessions.begin() as db:
        # Serialize concurrent retries of this occurrence, including skips.
        await db.execute(update(SettingAutomationRun).where(
            SettingAutomationRun.id == receipt_id,
        ).values(status=SettingAutomationRun.status))
        receipt = await db.get(SettingAutomationRun, receipt_id)
        if receipt.turn_id:
            return {'status': 'admitted', 'turn_id': receipt.turn_id}
        if receipt.status in {'skipped', 'denied'}:
            return {'status': receipt.status}
        db.info['require_idle_conversation'] = True
        try:
            async with db.begin_nested():
                turn = await database.submit(owner, session_id, message, db=db)
        except ConversationBusy:
            receipt.status = 'skipped'
        except HTTPException:
            receipt.status = 'denied'
        else:
            receipt.turn_id = turn.id
            receipt.status = 'admitted'
            return {'status': 'admitted', 'turn_id': turn.id}
        return {'status': receipt.status}


def replace_pinned(path, source, replacement):
    contents = path.read_text()
    if contents.count(source) != 1:
        raise SystemExit(f'Review the scheduled admission hook in {path.name}')
    path.write_text(contents.replace(source, replacement))


if __name__ == '__main__':
    # The pinned distribution has no idle-admission hook for interval schedules.
    package = Path(find_spec('moyai').origin).parent
    database = package / 'database.py'
    settings = package / 'settings_runtime.py'
    replace_pinned(database,
                   '                return existing\n            model = body.model or session.model',
                   '                return existing\n            from scheduled_turns import check_scheduled_turn\n'
                   '            await check_scheduled_turn(transaction, session)\n'
                   '            model = body.model or session.model')
    replace_pinned(settings, '        if receipt.status == "denied":\n            return {"status": "denied"}',
                   '        if receipt.status in {"denied", "skipped"}:\n            return {"status": receipt.status}')
    replace_pinned(settings, '''            turn = await runtime.database.submit(owner, snapshot["session_id"], NewMessage(
                prompt=snapshot["prompt"], model=snapshot["model"], connections=snapshot["connections"],
                request_key=f"automation:{identity}",
            ))''', '''            from scheduled_turns import submit_scheduled_turn
            return await submit_scheduled_turn(runtime.database, owner, snapshot["session_id"], NewMessage(
                prompt=snapshot["prompt"], model=snapshot["model"], connections=snapshot["connections"],
                request_key=f"automation:{identity}",
            ), identity)''')
    replace_pinned(settings, '                    SettingAutomationRun.id == identity,\n                ).values(status="denied"))',
                   '                    SettingAutomationRun.id == identity, SettingAutomationRun.status == "admitting",\n'
                   '                ).values(status="denied"))')
