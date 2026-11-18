"""Per-turn, text-only capabilities sourced from the local LiteLLM marketplace."""
import asyncio
from contextlib import closing
from datetime import timedelta
import hashlib
import json
import sqlite3

from pydantic_ai.capabilities import AbstractCapability, CombinedCapability
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

from skill_marketplace import fetch_skills


class GatewaySkill(AbstractCapability):
    defer_loading = True

    def __init__(self, name, description, body):
        self.id = name
        self.description = description
        self.body = body

    def get_instructions(self):
        return f'# Skill: {self.id}\n\n{self.body}'


def pinned_skills(turn, path='/data/engineering-skills.db', fetch=fetch_skills):
    # Tool workers reconstruct run capabilities independently. Keep one immutable
    # catalog per turn across workers, retries and restarts, deduplicated by digest.
    with closing(sqlite3.connect(path, timeout=15)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS catalogs (digest TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS turns (turn TEXT PRIMARY KEY, digest TEXT NOT NULL)')
        query = 'SELECT payload FROM turns JOIN catalogs USING (digest) WHERE turn=?'
        found = db.execute(query, (turn,)).fetchone()
        if found:
            return json.loads(found[0])
        payload = json.dumps(fetch(), sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO catalogs VALUES (?, ?)', (digest, payload))
        db.execute('INSERT OR IGNORE INTO turns VALUES (?, ?)', (turn, digest))
        return json.loads(db.execute(query, (turn,)).fetchone()[0])


@activity.defn(name='local_engineering_skill_catalog_v1')
async def turn_skills(turn: str) -> dict:
    return await asyncio.to_thread(pinned_skills, turn)


class GatewayCatalog(AbstractCapability):
    async def for_run(self, ctx):
        if workflow.in_workflow():
            if not workflow.patched('local-gateway-skill-catalog-v1'):
                return CombinedCapability([])
            skills = await workflow.execute_activity(turn_skills, ctx.run_id,
                start_to_close_timeout=timedelta(seconds=20), retry_policy=RetryPolicy(maximum_attempts=3))
        else:
            skills = await turn_skills(ctx.run_id)
        return CombinedCapability([GatewaySkill(name, **skill) for name, skill in skills.items()])
