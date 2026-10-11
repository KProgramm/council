import asyncio
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import HTTPException
from contracts.schema import Message, TradeoffMap
from agents.validation import REVIEWERS
from server.watcher import RepositoryWatch


def source(number, head='a', *, draft=False):
    return {'url': f'https://github.com/o/r/pull/{number}', 'repository': 'o/r',
            'number': number, 'title': f'PR {number}', 'author': 'author', 'draft': draft,
            'state': 'open', 'head_sha': head * 40, 'revision': 'b' * 40 + ':' + head * 40}


class WatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / '.council' / 'reviews.sqlite3'
        self.pulls = [source(2), source(1), source(3, draft=True)]
        self.calls = []
        async def listing(req):
            return {'repository': 'o/r', 'pull_requests': list(self.pulls), 'incomplete': False}
        async def load(req):
            self.calls.append(req.url)
            pr = next(p for p in self.pulls if p['url'] == req.url)
            return {'pull_request': dict(pr), 'diff': 'diff --git a/x b/x\n-old\n+new'}
        async def run(diff, review_id, publish):
            await publish(Message(review_id=review_id, round=0, agent='system', kind='diff', text=diff))
            for name in REVIEWERS:
                await publish(Message(review_id=review_id, round=1, agent=name, kind='assessment', position='approve', confidence=0.8, text='No concerns.'))
            verdict = TradeoffMap(review_id=review_id, outcome='consensus', final_positions={name: 'approve' for name in REVIEWERS}, resolved=[], unresolved=[], human_decisions=[])
            await publish(Message(review_id=review_id, round=1, agent='system', kind='verdict', text=verdict.model_dump_json()))
        self.watch = RepositoryWatch(self.path, run, list_prs=listing, load_pr=load)
        self.original_runner = run

    async def asyncTearDown(self):
        await self.watch.close()
        self.temp.cleanup()

    async def test_reviews_all_open_non_draft_prs_and_deduplicates_every_revision(self):
        await self.watch.connect('o/r')
        await self.watch.process_one()
        await self.watch.process_one()
        self.assertEqual(self.calls, [source(1)['url'], source(2)['url']])
        await self.watch.poll_once()
        self.assertFalse(await self.watch.process_one())
        self.assertEqual(self.watch.status()['counts']['completed'], 2)
        self.pulls[0] = source(2, 'c')
        await self.watch.poll_once()
        self.assertTrue(await self.watch.process_one())
        self.assertEqual(self.watch.status()['counts']['completed'], 3)
        job = self.watch.status()['jobs'][0]
        self.assertEqual(job['pull_request']['revision'], source(2, 'c')['revision'])
        self.assertEqual(self.watch.detail(job['id'])['messages'][0]['kind'], 'diff')

    async def test_new_revision_replaces_pending_work_and_closed_draft_jobs_are_skipped(self):
        await self.watch.connect('o/r')
        self.pulls[0] = source(2, 'c')
        self.pulls[1] = source(1, draft=True)
        await self.watch.poll_once()
        self.assertEqual(self.watch.status()['counts']['superseded'], 1)
        self.assertEqual(self.watch.status()['counts']['skipped'], 1)
        await self.watch.process_one()
        self.assertEqual(self.calls, [source(2)['url']])
        self.pulls[1] = source(1)
        await self.watch.poll_once()
        await self.watch.process_one()
        self.assertEqual(self.watch.status()['counts']['completed'], 2)

    async def test_push_after_queueing_is_replaced_before_the_council_sees_it(self):
        self.pulls = [source(1)]
        await self.watch.connect('o/r')
        self.pulls = [source(1, 'c')]
        self.watch.runner = AsyncMock(side_effect=self.original_runner)
        await self.watch.process_one()
        self.watch.runner.assert_not_awaited()
        await self.watch.process_one()
        self.watch.runner.assert_awaited_once()
        self.assertEqual(self.watch.status()['counts']['superseded'], 1)

    async def test_pause_finishes_current_review_and_prevents_the_next_from_starting(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def run(diff, review_id, publish):
            entered.set()
            await release.wait()
            await self.original_runner(diff, review_id, publish)
        self.watch.runner = run
        await self.watch.connect('o/r')
        task = asyncio.create_task(self.watch.process_one())
        await entered.wait()
        self.watch.pause()
        self.assertTrue(self.watch.status()['active'])
        release.set()
        await task
        self.assertFalse(await self.watch.process_one())
        self.assertEqual(self.watch.status()['counts']['completed'], 1)
        self.assertEqual(self.watch.status()['counts']['queued'], 1)

    async def test_concurrent_workers_cannot_overload_the_model(self):
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def run(diff, review_id, publish):
            nonlocal count
            count += 1
            self.assertEqual(count, 1)
            entered.set()
            await release.wait()
            await self.original_runner(diff, review_id, publish)
            count -= 1
        self.watch.runner = run
        await self.watch.connect('o/r')
        one = asyncio.create_task(self.watch.process_one())
        await entered.wait()
        two = asyncio.create_task(self.watch.process_one())
        await asyncio.sleep(0)
        self.assertEqual(len(self.calls), 1)
        release.set()
        await asyncio.gather(one, two)
        self.assertEqual(self.watch.status()['counts']['completed'], 2)

    async def test_restart_preserves_results_watch_and_recovers_interrupted_work(self):
        await self.watch.connect('o/r')
        await self.watch.process_one()
        queued = next(job for job in self.watch.status()['jobs'] if job['status'] == 'queued')
        self.watch.update(queued['id'], 'running')
        await self.watch.close()
        self.watch = RepositoryWatch(self.path, self.original_runner, list_prs=self.watch.list_prs, load_pr=self.watch.load_pr)
        self.assertTrue(self.watch.status()['enabled'])
        self.assertEqual(self.watch.status()['counts']['completed'], 1)
        self.assertEqual(self.watch.status()['counts']['queued'], 1)
        await self.watch.process_one()
        self.assertEqual(self.watch.status()['counts']['completed'], 2)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    async def test_oversized_pr_is_skipped_while_the_queue_keeps_running(self):
        original = self.watch.load_pr
        async def load(req):
            if req.url.endswith('/1'):
                raise HTTPException(413, 'PR exceeds the model size limit.')
            return await original(req)
        self.watch.load_pr = load
        await self.watch.connect('o/r')
        await self.watch.process_one()
        await self.watch.process_one()
        self.assertEqual(self.watch.status()['counts']['skipped'], 1)
        self.assertEqual(self.watch.status()['counts']['completed'], 1)
        await self.watch.poll_once()
        self.assertFalse(await self.watch.process_one())

    async def test_model_failure_is_redacted_pauses_and_can_be_retried(self):
        self.watch.runner = AsyncMock(side_effect=RuntimeError('secret-key-and-private-details'))
        await self.watch.connect('o/r')
        await self.watch.process_one()
        self.assertFalse(self.watch.status()['enabled'])
        self.assertNotIn('secret-key', json.dumps(self.watch.status()))
        failed = next(job for job in self.watch.status()['jobs'] if job['status'] == 'failed')
        self.watch.retry(failed['id'])
        self.watch.runner = self.original_runner
        await self.watch.connect('o/r')
        await self.watch.process_one()
        self.assertEqual(self.watch.detail(failed['id'])['status'], 'completed')

    async def test_missing_verdict_never_counts_as_a_success(self):
        self.watch.runner = AsyncMock()
        await self.watch.connect('o/r')
        await self.watch.process_one()
        self.assertEqual(self.watch.status()['counts']['failed'], 1)
        self.assertNotIn('completed', self.watch.status()['counts'])

    async def test_bad_github_credentials_pause_the_watch(self):
        await self.watch.connect('o/r')
        self.watch.list_prs = AsyncMock(side_effect=HTTPException(401, 'GitHub rejected the token.'))
        await self.watch.poll_once()
        self.assertFalse(self.watch.status()['enabled'])
        self.assertIn('token', self.watch.status()['error'])

    async def test_background_worker_runs_without_a_browser_and_persists_finished_results(self):
        await self.watch.connect('o/r')
        self.watch.start()
        async def wait_for_completion():
            while self.watch.status()['counts'].get('completed', 0) != 2:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_for_completion(), timeout=2)
        self.assertEqual(len(self.calls), 2)
        # There are no UI requests or sockets driving this worker.
        self.assertTrue(self.watch.status()['enabled'])

    async def test_agent_verdict_or_invalid_system_map_cannot_complete_a_review(self):
        for agent in ('security', 'system'):
            async def bad_run(diff, review_id, publish):
                await publish(Message(review_id=review_id, round=1, agent=agent, kind='verdict', text='not a tradeoff map'))
            self.watch.runner = bad_run
            await self.watch.connect('o/r')
            await self.watch.process_one()
            failed = next(job for job in self.watch.status()['jobs'] if job['status'] == 'failed')
            self.assertEqual(self.watch.detail(failed['id'])['status'], 'failed')
            self.watch.retry(failed['id'])

    async def test_second_server_cannot_run_against_the_same_queue(self):
        with self.assertRaisesRegex(RuntimeError, 'single server'):
            RepositoryWatch(self.path, self.original_runner)

    async def test_selected_scope_reviews_only_selected_prs_and_their_new_revisions(self):
        await self.watch.connect('o/r', 'selected', [2])
        await self.watch.process_one()
        self.pulls.extend([source(4)])
        self.pulls[0] = source(2, 'c')
        await self.watch.poll_once()
        await self.watch.process_one()
        self.assertEqual(self.calls, [source(2)['url'], source(2)['url']])
        self.assertFalse(await self.watch.process_one())
        self.assertEqual(self.watch.config()['selected_prs'], [2])

    async def test_invalid_selection_leaves_existing_watch_unchanged(self):
        await self.watch.connect('o/r')
        before = self.watch.config()
        for numbers in ([], [3], [999]):
            with self.assertRaises(HTTPException):
                await self.watch.connect('o/r', 'selected', numbers)
            self.assertEqual(self.watch.config(), before)

    async def test_changing_scope_holds_excluded_queue_and_reselection_restores_it(self):
        await self.watch.connect('o/r')
        await self.watch.connect('o/r', 'selected', [2])
        self.assertEqual(self.watch.status()['counts']['ignored'], 1)
        await self.watch.process_one()
        self.assertEqual(self.calls, [source(2)['url']])
        await self.watch.connect('o/r', 'selected', [1])
        await self.watch.process_one()
        self.assertEqual(self.watch.status()['counts']['completed'], 2)

    async def test_ignore_persists_across_pushes_restart_and_can_be_undone(self):
        await self.watch.connect('o/r')
        job = next(j for j in self.watch.status()['jobs'] if j['pull_request']['number'] == 2)
        self.watch.ignore(job['id'])
        self.pulls[0] = source(2, 'c')
        await self.watch.poll_once()
        await self.watch.process_one()
        self.assertEqual(self.calls, [source(1)['url']])
        await self.watch.close()
        self.watch = RepositoryWatch(self.path, self.original_runner, list_prs=self.watch.list_prs, load_pr=self.watch.load_pr)
        self.assertEqual(self.watch.status()['ignored_prs'], [2])
        self.watch.include(job['id'])
        await self.watch.poll_once()
        await self.watch.process_one()
        self.assertEqual(self.watch.status()['counts']['completed'], 2)
        self.assertEqual(self.watch.status()['ignored_prs'], [])

    async def test_ignore_during_diff_download_prevents_model_call(self):
        await self.watch.connect('o/r', 'selected', [2])
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.watch.load_pr
        async def load(req):
            entered.set()
            await release.wait()
            return await original(req)
        self.watch.load_pr = load
        self.watch.runner = AsyncMock()
        task = asyncio.create_task(self.watch.process_one())
        await entered.wait()
        job = self.watch.status()['jobs'][0]
        self.watch.ignore(job['id'])
        release.set()
        await task
        self.watch.runner.assert_not_awaited()
        self.assertEqual(self.watch.detail(job['id'])['status'], 'ignored')

    async def test_selected_scope_survives_restart(self):
        await self.watch.connect('o/r', 'selected', [2])
        await self.watch.close()
        self.watch = RepositoryWatch(self.path, self.original_runner, list_prs=self.watch.list_prs, load_pr=self.watch.load_pr)
        self.assertEqual(self.watch.config()['mode'], 'selected')
        self.assertEqual(self.watch.config()['selected_prs'], [2])
        await self.watch.process_one()
        self.assertEqual(self.calls, [source(2)['url']])

    async def test_restart_preserves_but_flags_invalid_historical_result(self):
        await self.watch.connect('o/r')
        await self.watch.process_one()
        completed = next(job for job in self.watch.status()['jobs'] if job['status'] == 'completed')
        messages = self.watch.detail(completed['id'])['messages']
        messages[1]['position'] = None
        self.watch.db.execute('UPDATE jobs SET messages=? WHERE id=?', (json.dumps(messages), completed['id']))
        self.watch.db.commit()
        await self.watch.close()
        self.watch = RepositoryWatch(self.path, self.original_runner, list_prs=self.watch.list_prs, load_pr=self.watch.load_pr)
        result = self.watch.detail(completed['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['valid_result'])
        self.assertEqual(result['messages'], messages, 'The original transcript remains available for inspection')
        self.assertFalse(self.watch.status()['enabled'])
