"""Server-owned repository watch and a durable, serial queue of PR revisions."""
import asyncio
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from contracts.schema import TradeoffMap
from pydantic import BaseModel, Field

from integrations.github import (RepositoryInput, PullRequestInput, github_client,
                                 list_pull_requests, load_pull_request, parse_repository)


def completed_map(messages):
    if not messages:
        return None
    last = messages[-1]
    if last.get('kind') != 'verdict' or last.get('agent') != 'system':
        return None
    try:
        return TradeoffMap.model_validate_json(last['text']).model_dump()
    except (ValueError, KeyError):
        return None


async def fetch_github(function, request):
    async for client in github_client():
        return await function(request, client)


class RepositoryWatch:
    def __init__(self, path: Path, runner, *, list_prs=None, load_pr=None):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        # A second server must not start another worker against the same queue.
        self.lock_file = path.with_suffix('.lock').open('a+b')
        os.chmod(path.with_suffix('.lock'), 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                self.lock_file.write(b'0')
                self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise RuntimeError('Another Council server is already using this review queue. Run a single server process.') from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, repository TEXT, enabled INTEGER);
            INSERT OR IGNORE INTO config VALUES (1, '', 0);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, repository TEXT, url TEXT, revision TEXT,
                source TEXT, status TEXT, error TEXT DEFAULT '', messages TEXT DEFAULT '[]',
                created REAL, updated REAL, UNIQUE(url, revision)
            );
        ''')
        # A stopped process cannot have a live review; retry its unfinished revision.
        self.db.execute("UPDATE jobs SET status='queued', messages='[]' WHERE status='running'")
        self.db.commit()
        self.runner = runner
        self.list_prs = list_prs or (lambda req: fetch_github(list_pull_requests, req))
        self.load_pr = load_pr or (lambda req: fetch_github(load_pull_request, req))
        self.poll_lock = asyncio.Lock()
        self.work_lock = asyncio.Lock()
        self.active = False
        self.error = ''
        self.last_checked = None
        self.next_check = 0
        self.wakeup = asyncio.Event()
        self.tasks = []

    def config(self):
        return dict(self.db.execute('SELECT repository, enabled FROM config WHERE id=1').fetchone())

    def status(self):
        config = self.config()
        # Always include live work and recent results, even with a large backlog.
        pending = self.db.execute("SELECT * FROM jobs WHERE repository=? AND status IN ('running','queued') ORDER BY (status='running') DESC, created LIMIT 50", (config['repository'],)).fetchall()
        recent = self.db.execute("SELECT * FROM jobs WHERE status NOT IN ('running','queued') ORDER BY updated DESC LIMIT 50").fetchall()
        rows = pending + recent
        counts = {row['status']: row['count'] for row in self.db.execute(
            "SELECT status, COUNT(*) AS count FROM jobs WHERE repository=? GROUP BY status", (config['repository'],))}
        jobs = []
        for row in rows:
            source = json.loads(row['source'])
            messages = json.loads(row['messages'])
            verdict = completed_map(messages)
            jobs.append({'id': row['id'], 'pull_request': source, 'status': row['status'],
                         'error': row['error'], 'outcome': verdict.get('outcome') if verdict else None})
        return {**config, 'enabled': bool(config['enabled']), 'active': self.active,
                'error': self.error, 'last_checked': self.last_checked, 'counts': counts, 'jobs': jobs}

    def enqueue(self, source):
        now = time.time()
        # A push replaces queued work, while an already running revision finishes honestly.
        self.db.execute("UPDATE jobs SET status='superseded', updated=? WHERE url=? AND revision!=? AND status='queued'",
                        (now, source['url'], source['revision']))
        self.db.execute("INSERT OR IGNORE INTO jobs (id,repository,url,revision,source,status,created,updated) VALUES (?,?,?,?,?,'queued',?,?)",
                        (uuid.uuid4().hex, source['repository'], source['url'], source['revision'], json.dumps(source), now, now))
        self.db.execute("UPDATE jobs SET status='queued', error='' WHERE url=? AND revision=? AND status='skipped' AND error='PR is closed or a draft.'",
                        (source['url'], source['revision']))
        self.db.commit()

    async def connect(self, repository):
        owner, repo = parse_repository(repository)
        repository = f'{owner}/{repo}'
        async with self.poll_lock:
            # Verify repository access before replacing the previous selection.
            data = await self.list_prs(RepositoryInput(repository=repository))
            self.db.execute('UPDATE config SET repository=?, enabled=1 WHERE id=1', (repository,))
            self.db.commit()
            self.ingest(data)
            self.next_check = time.time() + 120
        self.wakeup.set()
        return self.status()

    def ingest(self, data):
        repository = data['repository']
        eligible = {pr['url'] for pr in data['pull_requests'] if not pr['draft']}
        if not data.get('incomplete'):
            for row in self.db.execute("SELECT id,url FROM jobs WHERE repository=? AND status='queued'", (repository,)).fetchall():
                if row['url'] not in eligible:
                    self.db.execute("UPDATE jobs SET status='skipped', error='PR is closed or a draft.' WHERE id=?", (row['id'],))
        for pr in reversed(data['pull_requests']):
            if not pr['draft']:
                self.enqueue({**pr, 'repository': repository})
        self.db.commit()
        self.last_checked = time.time()
        self.error = 'Checking the newest 1,000 open PRs; this repository exceeds the watch limit.' if data.get('incomplete') else ''

    async def poll_once(self):
        async with self.poll_lock:
            config = self.config()
            if not config['enabled']:
                return
            try:
                data = await self.list_prs(RepositoryInput(repository=config['repository']))
                # Pause can happen while GitHub is responding.
                if self.config()['enabled']:
                    self.ingest(data)
            except HTTPException as exc:
                self.error = str(exc.detail)
                if exc.status_code in {401, 403, 404, 422}:
                    self.pause()
                self.next_check = time.time() + (600 if exc.status_code == 429 else 120)
                return
            except Exception:
                self.error = 'GitHub check failed. Council will retry in two minutes.'
            self.next_check = time.time() + 120
        self.wakeup.set()

    def pause(self):
        self.db.execute('UPDATE config SET enabled=0 WHERE id=1')
        self.db.commit()
        self.wakeup.set()
        return self.status()

    def update(self, job_id, status, error=''):
        self.db.execute('UPDATE jobs SET status=?, error=?, updated=? WHERE id=?', (status, error, time.time(), job_id))
        self.db.commit()

    def record(self, job_id, message):
        row = self.db.execute('SELECT messages FROM jobs WHERE id=?', (job_id,)).fetchone()
        messages = json.loads(row['messages'])
        messages.append(message.model_dump(mode='json'))
        self.db.execute('UPDATE jobs SET messages=? WHERE id=?', (json.dumps(messages), job_id))
        self.db.commit()

    def detail(self, job_id):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Review not found.')
        return {'id': row['id'], 'pull_request': json.loads(row['source']), 'status': row['status'],
                'error': row['error'], 'messages': json.loads(row['messages'])}

    def retry(self, job_id):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['status'] not in {'failed', 'skipped'}:
            raise HTTPException(409, 'Only failed or skipped reviews can be retried.')
        if row['repository'] != self.config()['repository']:
            raise HTTPException(409, 'Connect this review’s repository before retrying it.')
        self.db.execute("UPDATE jobs SET status='queued', error='', messages='[]' WHERE id=?", (job_id,))
        self.db.commit()
        self.wakeup.set()
        return self.status()

    async def process_one(self):
        # One review runs at a time, regardless of how many browser tabs are connected.
        async with self.work_lock:
            config = self.config()
            if not config['enabled']:
                return False
            row = self.db.execute("SELECT * FROM jobs WHERE repository=? AND status='queued' ORDER BY created LIMIT 1",
                                  (config['repository'],)).fetchone()
            if not row:
                return False
            job_id = row['id']
            self.active = True
            self.update(job_id, 'running')
            try:
                data = await self.load_pr(PullRequestInput(url=row['url']))
                source = data['pull_request']
                if source['state'] != 'open' or source.get('draft'):
                    self.update(job_id, 'skipped', 'PR is closed or a draft.')
                    return True
                if source['revision'] != row['revision']:
                    self.update(job_id, 'superseded')
                    self.enqueue(source)
                    return True
                if not self.config()['enabled'] or self.config()['repository'] != row['repository']:
                    self.update(job_id, 'queued')
                    return True
                self.db.execute('UPDATE jobs SET source=? WHERE id=?', (json.dumps(source), job_id))
                self.db.commit()
                async def publish(message):
                    self.record(job_id, message)
                await asyncio.wait_for(self.runner(data['diff'], job_id, publish), timeout=1800)
                messages = self.detail(job_id)['messages']
                if not completed_map(messages):
                    raise RuntimeError('No verdict')
                self.update(job_id, 'completed')
            except asyncio.CancelledError:
                self.update(job_id, 'queued')
                self.db.execute("UPDATE jobs SET messages='[]' WHERE id=?", (job_id,))
                self.db.commit()
                raise
            except HTTPException as exc:
                self.update(job_id, 'skipped' if exc.status_code in {413, 422, 404} else 'failed', str(exc.detail))
                if exc.status_code in {401, 403, 429}:
                    self.error = str(exc.detail)
                    self.pause()
            except Exception:
                # Provider exception text can include keys/URLs; keep it out of stored errors.
                self.update(job_id, 'failed', 'Council could not complete this review. Check the model connection, then retry.')
                self.error = 'Automatic reviews paused after a model failure. Check the connection, retry the failed review, and resume.'
                self.pause()
            finally:
                self.active = False
            return True

    async def poll_loop(self):
        while True:
            if self.config()['enabled'] and time.time() >= self.next_check:
                await self.poll_once()
            await asyncio.sleep(2)

    async def work_loop(self):
        while True:
            if await self.process_one():
                continue
            self.wakeup.clear()
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    def start(self):
        self.tasks = [asyncio.create_task(self.poll_loop()), asyncio.create_task(self.work_loop())]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.db.close()
        self.lock_file.close()


class WatchInput(BaseModel):
    repository: str = Field(min_length=1, max_length=2048)


def create_watch_router(get_watch, check_model):
    router = APIRouter(prefix='/watch', tags=['Automatic reviews'])

    @router.get('')
    async def status():
        return get_watch().status()

    @router.post('')
    async def connect(req: WatchInput):
        await check_model()
        return await get_watch().connect(req.repository)

    @router.post('/pause')
    async def pause():
        return get_watch().pause()

    @router.get('/reviews/{job_id}')
    async def detail(job_id: str):
        return get_watch().detail(job_id)

    @router.post('/reviews/{job_id}/retry')
    async def retry(job_id: str):
        return get_watch().retry(job_id)

    return router
