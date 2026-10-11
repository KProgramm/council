import json
import unittest
from unittest.mock import AsyncMock, patch

from agents.base import ReviewAgent
from agents.validation import InvalidReviewResponse, REVIEWERS, validate_assessment, validated_verdict
from contracts.schema import Message, Reference, TradeoffMap
from transport.memory_bus import MemoryBus


def assessment(agent='security', **overrides):
    return Message.model_validate({'review_id': 'live', 'agent': agent, 'round': 1,
        'kind': 'assessment', 'position': 'approve', 'confidence': 0.8,
        'claims': [], 'references': [], 'text': 'Visible changes are acceptable.', **overrides})


def diff():
    return Message(review_id='live', agent='system', round=0, kind='diff', text='diff --git a/x b/x\n-old\n+new')


class ReviewerValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bus = MemoryBus()
        self.agent = ReviewAgent('security', self.bus)
        await self.bus.publish(diff())

    async def test_missing_position_gets_corrective_retry_before_publication(self):
        invalid = assessment().model_dump()
        invalid['position'] = None
        call = AsyncMock(side_effect=[json.dumps(invalid), assessment().model_dump_json()])
        with patch.object(self.agent, '_complete', new=call):
            result = await self.agent.speak(1)
        self.assertEqual(result.position, 'approve')
        self.assertEqual(call.await_count, 2)
        self.assertIn('valid position', call.await_args.args[1])
        self.assertFalse(call.await_args.kwargs['use_cache'])

    async def test_invalid_response_is_an_error_instead_of_a_silent_pass(self):
        with patch.object(self.agent, '_complete', new=AsyncMock(return_value=assessment(position=None).model_dump_json())):
            with self.assertRaises(InvalidReviewResponse):
                await self.agent.speak(1)

    async def test_round_one_cannot_pass_but_later_round_can(self):
        with patch.object(self.agent, '_complete', new=AsyncMock(return_value='{"pass":true}')):
            with self.assertRaises(InvalidReviewResponse):
                await self.agent.speak(1)
            self.assertIsNone(await self.agent.speak(2))

    async def test_self_reference_is_rejected_and_retry_can_use_an_actual_peer(self):
        own = assessment()
        peer = assessment('performance')
        await self.bus.publish(own)
        await self.bus.publish(peer)
        invalid = assessment(round=2, kind='rebuttal', references=[Reference(agent='security', message_id=own.id, stance='challenge')])
        valid = assessment(round=2, kind='rebuttal', references=[Reference(agent='performance', message_id=peer.id, stance='challenge')])
        call = AsyncMock(side_effect=[invalid.model_dump_json(), valid.model_dump_json()])
        with patch.object(self.agent, '_complete', new=call):
            result = await self.agent.speak(2)
        self.assertEqual(result.references[0].agent, 'performance')
        self.assertIn('Never reference yourself', call.await_args.args[1])

    async def test_nonexistent_wrong_agent_and_future_peer_references_are_rejected(self):
        peer = assessment('performance')
        for reference in [Reference(agent='performance', message_id='invented', stance='agree'),
                          Reference(agent='quality', message_id=peer.id, stance='agree')]:
            with self.assertRaises(ValueError):
                validate_assessment(assessment(round=2, kind='rebuttal', references=[reference]), [peer])
        with self.assertRaises(ValueError):
            validate_assessment(assessment(round=2, kind='rebuttal', references=[Reference(agent='performance', message_id=peer.id, stance='agree')]), [peer.model_copy(update={'round': 2})])

    async def test_confidence_bounds_and_reviewer_verdicts_are_rejected(self):
        for overrides in [{'confidence': None}, {'confidence': 1.1}, {'confidence': -0.1}, {'confidence': float('nan')}, {'kind': 'verdict'}]:
            with self.assertRaises(ValueError):
                validate_assessment(assessment(**overrides), [diff()])


class ResultValidationTests(unittest.TestCase):
    def result(self, names=REVIEWERS, **overrides):
        messages = [diff()] + [assessment(name) for name in names]
        verdict = TradeoffMap(review_id='live', outcome='consensus', final_positions={name: 'approve' for name in names}, resolved=[], unresolved=[], human_decisions=[], **overrides)
        messages.append(Message(review_id='live', agent='system', round=1, kind='verdict', text=verdict.model_dump_json()))
        return messages

    def test_complete_agreement_is_valid(self):
        self.assertEqual(validated_verdict(self.result()).outcome, 'consensus')

    def test_missing_reviewer_cannot_be_successful_consensus(self):
        with self.assertRaises(ValueError):
            validated_verdict(self.result(REVIEWERS[:-1]))

    def test_verdict_cannot_change_an_agents_actual_position(self):
        messages = self.result()
        messages[1].position = 'block'
        with self.assertRaises(ValueError):
            validated_verdict(messages)
