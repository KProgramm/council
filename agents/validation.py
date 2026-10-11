"""Validate reviewer output without changing the shared Message contract."""
import math

from contracts.schema import Message

REVIEWERS = ('security', 'performance', 'compatibility', 'quality', 'product')


class InvalidReviewResponse(ValueError):
    """A model response did not meet the council's review protocol."""


def validate_assessment(message: Message, transcript: list[Message]) -> None:
    if message.agent not in REVIEWERS or message.position is None:
        raise ValueError('Every reviewer message must include a valid position.')
    if message.confidence is None or not math.isfinite(message.confidence) or not 0 <= message.confidence <= 1:
        raise ValueError('Every reviewer message must include confidence between 0 and 1.')
    if not message.text.strip():
        raise ValueError('Include a concise explanation in text.')
    if message.round == 1:
        if message.kind != 'assessment' or message.references:
            raise ValueError('Round 1 must be an independent assessment with no references.')
    elif message.round in {2, 3}:
        if message.kind not in {'rebuttal', 'evidence'} or not message.references:
            raise ValueError('Later rounds must respond to a peer using rebuttal/evidence and references, or pass.')
    else:
        raise ValueError('Reviewer messages must use rounds 1–3.')
    peers = {item.id: item for item in transcript if item.agent in REVIEWERS and item.agent != message.agent and item.round < message.round}
    for reference in message.references:
        peer = peers.get(reference.message_id)
        if not peer or peer.agent != reference.agent:
            raise ValueError('References must copy the agent and id of an actual earlier peer message. Never reference yourself.')
    for claim in message.claims:
        if claim.line is not None and claim.line < 1:
            raise ValueError('Claim line numbers start at 1; use null if uncertain.')


def validated_verdict(messages: list[Message]):
    """Require a real, complete debate before accepting a persisted result."""
    from contracts.schema import TradeoffMap
    from council.convergence import check
    if not messages or messages[-1].agent != 'system' or messages[-1].kind != 'verdict':
        raise ValueError('A system verdict is required.')
    if messages[0].agent != 'system' or messages[0].kind != 'diff' or messages[0].round != 0:
        raise ValueError('A review must begin with its actual diff.')
    if any(message.review_id != messages[0].review_id for message in messages):
        raise ValueError('All messages must belong to the same review.')
    prior = []
    initial = set()
    latest = {}
    for message in messages[:-1]:
        if message.agent in REVIEWERS:
            validate_assessment(message, prior)
            if message.round == 1:
                initial.add(message.agent)
            latest[message.agent] = message.position
        prior.append(message)
    if initial != set(REVIEWERS):
        raise ValueError('All five reviewers must provide an initial assessment.')
    verdict = TradeoffMap.model_validate_json(messages[-1].text)
    if verdict.review_id != messages[-1].review_id or verdict.final_positions != latest:
        raise ValueError('The verdict must reflect the actual reviewer positions.')
    if verdict.outcome == 'consensus' and check(prior) != 'consensus':
        raise ValueError('Consensus requires all five reviewers to agree with sufficient confidence.')
    return verdict
