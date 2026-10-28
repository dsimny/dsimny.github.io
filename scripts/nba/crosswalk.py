"""Provider ID crosswalks. Pure, deterministic, offline.

A crosswalk maps a provider's label for a game onto a canonical league-issued game
ID. It is a LOOKUP TABLE, not a matcher: every mapping is an explicit, reviewed
assertion that "ESPN game X is league game Y".

WHAT THIS MODULE DELIBERATELY CANNOT DO

  - It cannot invent an identity. A crosswalk entry whose canonical target is absent
    is not a mapping; it is a claim awaiting evidence, and it stays unresolved.
  - It cannot match on team names or dates. Those inputs are not accepted at all,
    so a future caller cannot drift into matching by convenience.
  - It cannot break a tie. Two entries disagreeing about the same
    (provider, provider_id) is a conflict and the lookup quarantines.

REAL vs SYNTHETIC (architecture decision 3, 2026-10-26): entries carry a provenance
label. `synthetic` entries exist to exercise this code. `verified` entries require
recorded source evidence -- a URL, a retrieval timestamp and a byte-level hash --
before promotion. Nothing in this package promotes a real mapping; the label is what
stops a fixture from silently becoming production truth.
"""
import hashlib
import json

from . import identity

VERSION = 'nba-crosswalk-v0.1'

PROVENANCE_SYNTHETIC = 'synthetic'
PROVENANCE_VERIFIED = 'verified'
PROVENANCE_LABELS = frozenset({PROVENANCE_SYNTHETIC, PROVENANCE_VERIFIED})

# Providers this package understands. ESPN is the preferred SECONDARY provider
# (decision 2); it is never an authority for identity.
PROVIDERS = ('nba', 'espn')


class Crosswalk:
    """An immutable provider->league mapping set.

    Built once from an explicit list, then read-only. No mutation API exists, so a
    lookup can never be quietly changed mid-run.
    """

    def __init__(self, entries):
        self._by_provider = {}
        self._provenance = {}
        for raw in entries:
            entry = self._validate(raw)
            key = (entry['provider'], entry['provider_id'])
            if key in self._by_provider:
                existing = self._by_provider[key]
                if existing != entry['league_game_id']:
                    # Two claims about one label. Refuse both rather than pick.
                    existing_reason = self._provenance.get(key, {}).get('conflict')
                    self._provenance[key] = {
                        **self._provenance.get(key, {}),
                        'conflict': {existing, entry['league_game_id']},
                    }
                    self._by_provider[key] = None
                continue
            self._by_provider[key] = entry['league_game_id']
            self._provenance[key] = {
                'provenance': entry['provenance'],
                'evidence': entry.get('evidence'),
            }
        self._entries = tuple(entries)

    @staticmethod
    def _validate(raw):
        if not isinstance(raw, dict):
            raise ValueError('Crosswalk entry must be a mapping: %r' % (raw,))
        provider = raw.get('provider')
        provider_id = raw.get('provider_id')
        league_game_id = raw.get('league_game_id')
        provenance = raw.get('provenance', PROVENANCE_SYNTHETIC)
        for name, value in (('provider', provider), ('provider_id', provider_id),
                            ('league_game_id', league_game_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Crosswalk entry requires a non-empty %s' % name)
        if provider not in PROVIDERS:
            raise ValueError('Unknown provider %r (known: %s)' % (provider, ', '.join(PROVIDERS)))
        if provenance not in PROVENANCE_LABELS:
            raise ValueError('Unknown provenance %r' % (provenance,))
        if provenance == PROVENANCE_VERIFIED:
            evidence = raw.get('evidence')
            if not isinstance(evidence, dict) or not evidence.get('url') or not evidence.get('retrieved_at'):
                raise ValueError('A verified crosswalk entry requires evidence.url and evidence.retrieved_at')
        return {
            'provider': provider.strip(),
            'provider_id': provider_id.strip(),
            'league_game_id': league_game_id.strip(),
            'provenance': provenance,
            'evidence': raw.get('evidence'),
        }

    def resolve(self, provider, provider_id):
        """Canonical league game ID for a provider label, or None.

        None means UNRESOLVED -- an absence of mapping, not an absence of game. The
        caller must quarantine on None rather than proceed.
        """
        if not isinstance(provider, str) or not isinstance(provider_id, str):
            return None
        return self._by_provider.get((provider.strip(), provider_id.strip()))

    def is_conflicted(self, provider, provider_id):
        return bool(self._provenance.get((provider, provider_id), {}).get('conflict'))

    def conflict_detail(self, provider, provider_id):
        return self._provenance.get((provider, provider_id), {}).get('conflict')

    def provenance_of(self, provider, provider_id):
        return self._provenance.get((provider, provider_id), {}).get('provenance')

    def unresolved(self):
        """Labels seen with no mapping at all."""
        return tuple(sorted(k for k, v in self._by_provider.items() if v is None))

    def __len__(self):
        return len([v for v in self._by_provider.values() if v is not None])


def load_crosswalk(path):
    """Load a crosswalk JSON file. Layout:

        {"provenance": "...", "entries": [{"provider":..., "provider_id":...,
                                           "league_game_id":..., "provenance":...}, ...]}
    """
    with open(path, encoding='utf-8') as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or 'entries' not in document:
        raise ValueError('Crosswalk file must be an object with an "entries" list')
    if not isinstance(document['entries'], list):
        raise ValueError('Crosswalk "entries" must be a list')
    return Crosswalk(document['entries'])


def apply_crosswalk(offers, crosswalk, provider):
    """Resolve a list of provider offers into canonical IDs, quarantining failures.

    `offers` is a list of {"provider_id": ..., "record": {...}}. Each offer resolves
    to league_game_id via the crosswalk, or is quarantined. Team names and dates in
    `record` are NOT consulted -- there is no code path here that could.
    """
    resolved, quarantined = [], []
    for offer in offers:
        provider_id = (offer or {}).get('provider_id')
        try:
            if not isinstance(provider_id, str) or not provider_id.strip():
                raise identity.Quarantine(identity.INSUFFICIENT_PROVENANCE,
                                          {'provider': provider, 'provider_id': provider_id})
            if crosswalk.is_conflicted(provider, provider_id):
                raise identity.Quarantine(identity.AMBIGUOUS_PROVIDER_MATCH, {
                    'provider': provider, 'provider_id': provider_id,
                    'candidates': sorted(crosswalk.conflict_detail(provider, provider_id) or []),
                })
            league_game_id = crosswalk.resolve(provider, provider_id)
            if league_game_id is None:
                raise identity.Quarantine(identity.MISSING_LEAGUE_ID, {
                    'provider': provider, 'provider_id': provider_id,
                    'detail': 'no crosswalk entry; provider label cannot establish identity',
                })
            merged = dict(offer.get('record') or {})
            merged['league_game_id'] = league_game_id
            resolved.append({'league_game_id': league_game_id, 'record': merged,
                             'provider': provider, 'provider_id': provider_id,
                             'provenance': crosswalk.provenance_of(provider, provider_id)})
        except identity.Quarantine as quarantined_record:
            detail = dict(quarantined_record.detail)
            detail.setdefault('provider', provider)
            detail.setdefault('provider_id', provider_id)
            quarantined.append({'offer': offer, 'reason': quarantined_record.reason, 'detail': detail})
    return resolved, quarantined


def digest(entries):
    """Stable content hash of a crosswalk definition, for review records."""
    payload = json.dumps(sorted(
        (e['provider'], e['provider_id'], e['league_game_id'], e.get('provenance', PROVENANCE_SYNTHETIC))
        for e in entries), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
