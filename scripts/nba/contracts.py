"""Pure Package 1 invariants. This module does not generate projections or picks."""
from datetime import datetime, timezone
import math

VERSION = 'nba-contract-v0.1'
PHASES = {'PRESEASON', 'REGULAR_SEASON', 'CUP', 'PLAY_IN', 'POSTSEASON'}
CUP_ROUNDS = {'GROUP', 'QUARTERFINAL', 'SEMIFINAL', 'CHAMPIONSHIP'}


def utc(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Timezone required')
    return stamp.astimezone(timezone.utc)


def counts_regular(phase, cup_round=None):
    if phase not in PHASES:
        raise ValueError('Unknown schedule phase')
    if phase == 'CUP':
        if cup_round not in CUP_ROUNDS:
            raise ValueError('Cup round required')
        return cup_round != 'CHAMPIONSHIP'
    if cup_round is not None:
        raise ValueError('Cup round on non-Cup game')
    return phase == 'REGULAR_SEASON'


def require_available(available_at, ingested_at, cutoff):
    if utc(available_at) > utc(cutoff) or utc(ingested_at) > utc(cutoff):
        raise ValueError('Input unavailable at decision cutoff')


def decimal_odds(price):
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise ValueError('Invalid American odds')
    if not math.isfinite(price) or abs(price) < 100:
        raise ValueError('Invalid American odds')
    return 1 + (price / 100 if price > 0 else 100 / -price)


def expected_return(win, loss, push, price):
    probs = (win, loss, push)
    if any(isinstance(x, bool) or not isinstance(x, (int, float))
           or not math.isfinite(x) or not 0 <= x <= 1 for x in probs):
        raise ValueError('Invalid probability')
    if not math.isclose(sum(probs), 1, abs_tol=1e-9, rel_tol=0):
        raise ValueError('Win/loss/push must sum to one')
    return win * (decimal_odds(price) - 1) - loss
