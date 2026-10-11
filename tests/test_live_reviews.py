"""Live reviews must report failures and never substitute a recorded review."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from server.app import app, ReviewStream, run_review, pump


class LiveReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_socket_gets_progress_and_full_error_before_close(self):
        stream = ReviewStream()
        await stream.publish_progress({'phase': 'reviewing', 'round': 1, 'agents': {'security': 'reviewing'}})
        await stream.publish_progress({'agents': {'performance': 'complete'}})
        stream.error = 'The security reviewer returned invalid output twice. ' * 5
        stream.close()
        socket = AsyncMock()
        await pump(socket, stream)
        self.assertEqual(socket.send_json.await_args_list[0].args[0]['agents'], {'security': 'reviewing', 'performance': 'complete'})
        self.assertEqual(socket.send_json.await_args_list[1].args[0], {'type': 'error', 'message': stream.error})
        self.assertLessEqual(len(socket.close.await_args.kwargs['reason'].encode()), 123)
        self.assertFalse(stream.queues)

    async def test_unavailable_runtime_returns_error_without_recorded_messages(self):
        stream = ReviewStream()
        with patch('agents.runtime.run_council', new=AsyncMock(side_effect=NotImplementedError)):
            await run_review('real diff', 'real-review', stream)
        self.assertTrue(stream.done)
        self.assertEqual(stream.history, [])
        self.assertEqual(stream.error, 'Council runtime is unavailable.')

    async def test_recorded_transcripts_and_replay_are_not_served(self):
        self.assertNotIn('/replay', {getattr(route, 'path', '') for route in app.routes})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
            response = await client.get('/fixtures/transcripts/demo.json')
        self.assertEqual(response.status_code, 404)

    async def test_bad_model_connection_does_not_start_a_live_review(self):
        from fastapi import HTTPException
        with patch('server.app.check_model_connection', new=AsyncMock(side_effect=HTTPException(502, 'Model unavailable.'))):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
                response = await client.post('/reviews', json={'diff': 'real diff'})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['detail'], 'Model unavailable.')
