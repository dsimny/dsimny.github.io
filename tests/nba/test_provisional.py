"""M0 regression tests for provisional slot identities (nba-provisional-v0.1).

WRITTEN BEFORE THE IMPLEMENTATION. Run against a tree where `nba.provisional` does not
exist, every test here errors on import -- the demonstration that the behaviour did not
exist, rather than that it was untested.

WHAT THIS PINS.

  * Slot identities are durable: minted from competition + structural position (+ an
    immutable anchor), never from dates, windows, opponents or venues.
  * A slot can never become a canonical game without independently verified league
    identity, and a slot key can never be smuggled in AS a league game id.
  * Expected-slot declarations carry independent provenance; deriving an expectation from
    what we captured is refused outright.
  * Resolution cardinality is 0..n and is never assumed to be 1:1.
  * Slot reconciliation is separate from the canonical game-count denominator.

SYNTHETIC ONLY. All identifiers, evidence URLs and payloads here are invented. The
evidence URL uses the reserved `.invalid` TLD precisely so it cannot be mistaken for a
real retrieved source. No real NBA-derived data is used.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from nba import completeness as comp
from nba import identity as ident
from nba import provisional as prov
from nba import schedule as sch


EVIDENCE = {'url': 'https://synthetic.invalid/m0/cup-slot', 'retrieved_at': '2026-11-01T12:00:00Z'}
OBS = 'obs.w1.00000001'
GAME = {'league_game_id': '0022600001', 'home_team_id': 'TEAM-BOS', 'away_team_id': 'TEAM-NYK'}
GAME2 = {'league_game_id': '0022600002', 'home_team_id': 'TEAM-LAL', 'away_team_id': 'TEAM-MIA'}
QF1 = prov.mint_slot_id('CUP_KNOCKOUT', 'QF/1')


def declared(*slot_ids, competition='CUP_KNOCKOUT', source_ref='https://synthetic.invalid/announcement'):
    return dict(competition=competition, declared_slots=list(slot_ids), source_ref=source_ref,
                observation_id=OBS, declared_at='2026-11-01T12:00:00Z')


class SlotIdentity(unittest.TestCase):
    def test_slot_id_is_stable_when_the_window_moves(self):
        # The window is an attribute, not key material: moving it must not re-identify the slot.
        a = prov.SlotRegistry()
        b = prov.SlotRegistry()
        one = a.declare_slot(competition='CUP_KNOCKOUT', position='QF/1',
                             provisional_reason='unassigned at publication',
                             first_observed_observation_id=OBS,
                             expected_window_start='2026-12-04', expected_window_end='2026-12-05')
        two = b.declare_slot(competition='CUP_KNOCKOUT', position='QF/1',
                             provisional_reason='unassigned at publication',
                             first_observed_observation_id=OBS,
                             expected_window_start='2026-12-06', expected_window_end='2026-12-07')
        self.assertEqual(one['slot_id'], two['slot_id'])

    def test_slot_id_is_deterministic_and_pure(self):
        self.assertEqual(prov.mint_slot_id('CUP_KNOCKOUT', 'QF/1'),
                         prov.mint_slot_id('CUP_KNOCKOUT', 'QF/1'))
        self.assertNotEqual(prov.mint_slot_id('CUP_KNOCKOUT', 'QF/1'),
                            prov.mint_slot_id('CUP_KNOCKOUT', 'QF/2'))

    def test_slot_id_rejects_unknown_competition(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            prov.mint_slot_id('EXHIBITION', 'QF/1')
        self.assertEqual(ctx.exception.reason, prov.INVALID_SLOT_ID)

    def test_slot_id_rejects_date_bearing_positions(self):
        for position in ('2026-12-04', '20261204', '12/04'):
            with self.subTest(position=position), self.assertRaises(prov.ProvisionalError) as ctx:
                prov.mint_slot_id('CUP_KNOCKOUT', position)
            self.assertEqual(ctx.exception.reason, prov.DATE_BEARING_POSITION)

    def test_slot_id_rejects_prose_and_venue_material(self):
        # Venue and opponent strings are not structural positions.
        for position in ('Paris, FR', 'at BOS', 'boston'):
            with self.subTest(position=position), self.assertRaises(prov.ProvisionalError) as ctx:
                prov.mint_slot_id('CUP_KNOCKOUT', position)
            self.assertEqual(ctx.exception.reason, prov.INVALID_SLOT_ID)

    def test_slot_id_rejects_empty_position(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            prov.mint_slot_id('CUP_KNOCKOUT', '')
        self.assertEqual(ctx.exception.reason, prov.MISSING_POSITION)

    def test_slot_id_rejects_placeholder_anchor(self):
        for anchor in ('TBD', 'UNKNOWN', 'PENDING', '*', '-'):
            with self.subTest(anchor=anchor), self.assertRaises(prov.ProvisionalError) as ctx:
                prov.mint_slot_id('REGULAR_ASSIGNMENT_WINDOW', '1', anchor=anchor)
            self.assertEqual(ctx.exception.reason, prov.PLACEHOLDER_ANCHOR)

    def test_slot_id_accepts_a_known_anchor(self):
        minted = prov.mint_slot_id('REGULAR_ASSIGNMENT_WINDOW', '1', anchor='TEAM-BOS')
        self.assertIn('TEAM-BOS', minted)

    def test_foreign_keys_are_not_valid_slot_ids(self):
        for foreign in ('0022600001', 'obs.w1.00000001', '', 'CUP_KNOCKOUT'):
            with self.subTest(foreign=foreign), self.assertRaises(prov.ProvisionalError):
                prov.validate_slot_id(foreign)

    def test_minted_ids_validate(self):
        self.assertEqual(prov.validate_slot_id(QF1), QF1)


class SlotIsNeverAGame(unittest.TestCase):
    def setUp(self):
        self.reg = prov.SlotRegistry()
        self.slot = self.reg.declare_slot(competition='CUP_KNOCKOUT', position='QF/1',
                                          provisional_reason='unassigned at publication',
                                          first_observed_observation_id=OBS)

    def test_slot_key_can_never_be_used_as_a_league_game_id(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_to_game(self.slot['slot_id'], league_game_id=self.slot['slot_id'],
                                     home_team_id=GAME['home_team_id'],
                                     away_team_id=GAME['away_team_id'],
                                     observation_id=OBS, evidence=EVIDENCE)
        self.assertEqual(ctx.exception.reason, prov.SLOT_KEY_AS_IDENTITY)

    def test_observation_key_can_never_be_used_as_a_league_game_id(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_to_game(self.slot['slot_id'], league_game_id=OBS,
                                     home_team_id=GAME['home_team_id'],
                                     away_team_id=GAME['away_team_id'],
                                     observation_id=OBS, evidence=EVIDENCE)
        self.assertEqual(ctx.exception.reason, prov.SLOT_KEY_AS_IDENTITY)

    def test_resolution_requires_identity_evidence(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_to_game(self.slot['slot_id'], league_game_id=GAME['league_game_id'],
                                     home_team_id=GAME['home_team_id'],
                                     away_team_id=GAME['away_team_id'],
                                     observation_id=OBS, evidence={})
        self.assertEqual(ctx.exception.reason, prov.MISSING_EVIDENCE)

    def test_incomplete_evidence_refused(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_to_game(self.slot['slot_id'], league_game_id=GAME['league_game_id'],
                                     home_team_id=GAME['home_team_id'],
                                     away_team_id=GAME['away_team_id'],
                                     observation_id=OBS,
                                     evidence={'url': 'https://synthetic.invalid/x'})
        self.assertEqual(ctx.exception.reason, prov.MISSING_EVIDENCE)

    def test_resolution_refuses_when_the_package2a_authority_refuses(self):
        # No league id at all -> Package 2A quarantines -> M0 refuses to resolve.
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_to_game(self.slot['slot_id'], league_game_id='',
                                     home_team_id=GAME['home_team_id'],
                                     away_team_id=GAME['away_team_id'],
                                     observation_id=OBS, evidence=EVIDENCE)
        self.assertEqual(ctx.exception.reason, prov.UNVERIFIED_IDENTITY)
        self.assertEqual(ctx.exception.detail.get('package2a_reason'), ident.MISSING_LEAGUE_ID)

    def test_resolution_refuses_identical_participants(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_to_game(self.slot['slot_id'], league_game_id=GAME['league_game_id'],
                                     home_team_id=GAME['home_team_id'],
                                     away_team_id=GAME['home_team_id'],
                                     observation_id=OBS, evidence=EVIDENCE)
        self.assertEqual(ctx.exception.reason, prov.UNVERIFIED_IDENTITY)
        self.assertEqual(ctx.exception.detail.get('package2a_reason'), ident.INVALID_TEAM_ID)

    def test_unknown_slot_refused(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_to_game(prov.mint_slot_id('CUP_KNOCKOUT', 'SF/1'),
                                     league_game_id=GAME['league_game_id'],
                                     home_team_id=GAME['home_team_id'],
                                     away_team_id=GAME['away_team_id'],
                                     observation_id=OBS, evidence=EVIDENCE)
        self.assertEqual(ctx.exception.reason, prov.UNKNOWN_SLOT)

    def test_resolution_does_not_mutate_the_slot(self):
        before = dict(self.reg.slots[0])
        self.reg.resolve_to_game(self.slot['slot_id'], league_game_id=GAME['league_game_id'],
                                 home_team_id=GAME['home_team_id'],
                                 away_team_id=GAME['away_team_id'],
                                 observation_id=OBS, evidence=EVIDENCE)
        self.assertEqual(dict(self.reg.slots[0]), before)
        self.assertTrue(self.reg.verify_digests())

    def test_no_participant_inference_api_exists(self):
        for guess in ('infer_participants', 'guess_participants', 'participants_from_bracket'):
            self.assertFalse(hasattr(prov, guess), guess)

    def test_no_package2a_game_record_is_created(self):
        self.reg.resolve_to_game(self.slot['slot_id'], league_game_id=GAME['league_game_id'],
                                 home_team_id=GAME['home_team_id'],
                                 away_team_id=GAME['away_team_id'],
                                 observation_id=OBS, evidence=EVIDENCE)
        # M0 records the LINK, not the game.
        self.assertEqual(len(self.reg.resolutions), 1)
        self.assertNotIn('canonical_game', self.reg.resolutions[0])
        self.assertEqual(self.reg.resolutions[0]['target_ref'], GAME['league_game_id'])


class Declarations(unittest.TestCase):
    def setUp(self):
        self.reg = prov.SlotRegistry()

    def test_declaration_requires_independent_provenance(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.declare_expectation(competition='CUP_KNOCKOUT', declared_slots=[QF1],
                                         source_ref='', observation_id=OBS,
                                         declared_at='2026-11-01T12:00:00Z')
        self.assertEqual(ctx.exception.reason, prov.DECLARATION_WITHOUT_PROVENANCE)

    def test_declaration_requires_an_observation_reference(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.declare_expectation(competition='CUP_KNOCKOUT', declared_slots=[QF1],
                                         source_ref='https://synthetic.invalid/announcement',
                                         observation_id='', declared_at='2026-11-01T12:00:00Z')
        self.assertEqual(ctx.exception.reason, prov.DECLARATION_WITHOUT_PROVENANCE)

    def test_declaration_never_derived_from_captured_counts(self):
        # The function that would make the denominator circular refuses to run at all.
        self.reg.declare_slot(competition='CUP_KNOCKOUT', position='QF/1',
                              provisional_reason='unassigned', first_observed_observation_id=OBS)
        with self.assertRaises(prov.ProvisionalError) as ctx:
            prov.expectation_from_observed_slots(self.reg)
        self.assertEqual(ctx.exception.reason, prov.DERIVED_EXPECTATION_REFUSED)

    def test_duplicate_declared_slot_within_a_declaration_refused(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.declare_expectation(competition='CUP_KNOCKOUT', declared_slots=[QF1, QF1],
                                         source_ref='https://synthetic.invalid/announcement',
                                         observation_id=OBS, declared_at='2026-11-01T12:00:00Z')
        self.assertEqual(ctx.exception.reason, prov.DUPLICATE_IDENTIFIER)

    def test_two_live_declarations_for_one_slot_refused(self):
        self.reg.declare_expectation(**declared(QF1))
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.declare_expectation(**declared(QF1))
        self.assertEqual(ctx.exception.reason, prov.DUPLICATE_IDENTIFIER)

    def test_redeclaration_supersedes_and_prior_stays_readable(self):
        first = self.reg.declare_expectation(**declared(QF1))
        second = self.reg.declare_expectation(competition='CUP_KNOCKOUT',
                                              declared_slots=[prov.mint_slot_id('CUP_KNOCKOUT', 'QF/1')],
                                              source_ref='https://synthetic.invalid/announcement-2',
                                              observation_id='obs.w1.00000002',
                                              declared_at='2026-11-02T12:00:00Z',
                                              supersedes_expectation_id=first['expectation_id'])
        self.assertEqual(len(self.reg.expectations), 2)
        self.assertEqual(self.reg.expectations[0]['expectation_id'], first['expectation_id'])
        self.assertEqual(second['supersedes_expectation_id'], first['expectation_id'])

    def test_unknown_supersedes_refused(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.declare_expectation(competition='CUP_KNOCKOUT', declared_slots=[QF1],
                                         source_ref='https://synthetic.invalid/a', observation_id=OBS,
                                         declared_at='2026-11-01T12:00:00Z',
                                         supersedes_expectation_id='EXP-nope')
        self.assertEqual(ctx.exception.reason, prov.UNKNOWN_EXPECTATION)


class ResolutionCardinality(unittest.TestCase):
    def setUp(self):
        self.reg = prov.SlotRegistry()
        self.slot = self.reg.declare_slot(competition='CUP_KNOCKOUT', position='QF/1',
                                          provisional_reason='unassigned at publication',
                                          first_observed_observation_id=OBS)
        self.sid = self.slot['slot_id']

    def _game(self, game=GAME, role=prov.ROLE_ORIGIN, obs=OBS):
        return self.reg.resolve_to_game(self.sid, league_game_id=game['league_game_id'],
                                        home_team_id=game['home_team_id'],
                                        away_team_id=game['away_team_id'],
                                        observation_id=obs, evidence=EVIDENCE, role=role)

    def test_pending_slot_state(self):
        self.assertEqual(self.reg.slot_state(self.sid), prov.SLOT_PENDING)

    def test_zero_resolution_is_evidenced_empty(self):
        self.reg.resolve_empty(self.sid, observation_id=OBS, evidence=EVIDENCE)
        self.assertEqual(self.reg.slot_state(self.sid), prov.SLOT_RESOLVED_EMPTY)

    def test_resolved_empty_is_distinct_from_pending(self):
        self.assertNotEqual(prov.SLOT_PENDING, prov.SLOT_RESOLVED_EMPTY)

    def test_unevidenced_empty_refused(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_empty(self.sid, observation_id='', evidence=EVIDENCE)
        self.assertEqual(ctx.exception.reason, prov.MISSING_EVIDENCE)

    def test_unevidenced_empty_without_evidence_dict_refused(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_empty(self.sid, observation_id=OBS, evidence={})
        self.assertEqual(ctx.exception.reason, prov.MISSING_EVIDENCE)

    def test_one_resolution(self):
        self._game()
        self.assertEqual(self.reg.slot_state(self.sid), prov.SLOT_RESOLVED)
        self.assertEqual(len(self.reg.resolutions_for(self.sid)), 1)

    def test_multiple_resolutions_origin_plus_successor(self):
        self._game()
        self._game(game=GAME2, role=prov.ROLE_SUCCESSOR, obs='obs.w1.00000002')
        self.assertEqual(len(self.reg.resolutions_for(self.sid)), 2)
        self.assertEqual(self.reg.slot_state(self.sid), prov.SLOT_RESOLVED)

    def test_second_origin_refused(self):
        self._game()
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self._game(game=GAME2, role=prov.ROLE_ORIGIN)
        self.assertEqual(ctx.exception.reason, prov.MULTIPLE_ORIGINS)

    def test_duplicate_resolution_refused(self):
        self._game()
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self._game(role=prov.ROLE_ALTERNATE)
        self.assertEqual(ctx.exception.reason, prov.DUPLICATE_RESOLUTION)

    def test_slot_to_slot_supersession(self):
        target = self.reg.declare_slot(competition='CUP_KNOCKOUT', position='QF/1-R',
                                       provisional_reason='re-issued window',
                                       first_observed_observation_id='obs.w1.00000003')
        self.reg.resolve_to_slot(self.sid, target_slot_id=target['slot_id'],
                                 observation_id='obs.w1.00000003', evidence=EVIDENCE)
        self.assertEqual(self.reg.slot_state(self.sid), prov.SLOT_SUPERSEDED)
        self.assertEqual(self.reg.slot_state(target['slot_id']), prov.SLOT_PENDING)

    def test_superseded_slot_refuses_further_resolution(self):
        target = self.reg.declare_slot(competition='CUP_KNOCKOUT', position='QF/1-R',
                                       provisional_reason='re-issued window',
                                       first_observed_observation_id='obs.w1.00000003')
        self.reg.resolve_to_slot(self.sid, target_slot_id=target['slot_id'],
                                 observation_id='obs.w1.00000003', evidence=EVIDENCE)
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self._game()
        self.assertEqual(ctx.exception.reason, prov.SLOT_ALREADY_SUPERSEDED)

    def test_contradictory_resolution_refused(self):
        self.reg.resolve_empty(self.sid, observation_id=OBS, evidence=EVIDENCE)
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self._game()
        self.assertEqual(ctx.exception.reason, prov.CONTRADICTORY_RESOLUTION)

    def test_resolution_to_unknown_slot_refused(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self.reg.resolve_to_slot(self.sid, target_slot_id=prov.mint_slot_id('CUP_KNOCKOUT', 'SF/1'),
                                     observation_id=OBS, evidence=EVIDENCE)
        self.assertEqual(ctx.exception.reason, prov.UNKNOWN_SLOT)

    def test_unknown_role_refused(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            self._game(role='primary')
        self.assertEqual(ctx.exception.reason, prov.INVALID_ROLE)


class Reconciliation(unittest.TestCase):
    def _registry(self, observed=True, declared_slots=None):
        reg = prov.SlotRegistry()
        if observed:
            reg.declare_slot(competition='CUP_KNOCKOUT', position='QF/1',
                             provisional_reason='unassigned', first_observed_observation_id=OBS)
        reg.declare_expectation(**declared(*(declared_slots or [QF1])))
        return reg

    def test_reconciles_when_declared_and_observed_match(self):
        report = prov.reconcile_slots(self._registry())
        self.assertEqual(report['pending'], [QF1])
        self.assertEqual(report['missing_vs_declaration'], [])
        self.assertEqual(report['undeclared_observations'], [])
        self.assertTrue(report['reconciles'])

    def test_declared_but_unobserved_is_a_missing_finding(self):
        report = prov.reconcile_slots(self._registry(observed=False))
        self.assertEqual(report['missing_vs_declaration'], [QF1])
        self.assertFalse(report['reconciles'])

    def test_observed_but_undeclared_is_a_finding(self):
        reg = self._registry()
        extra = reg.declare_slot(competition='CUP_KNOCKOUT', position='SF/1',
                                 provisional_reason='unassigned', first_observed_observation_id=OBS)
        report = prov.reconcile_slots(reg)
        self.assertEqual(report['undeclared_observations'], [extra['slot_id']])
        self.assertFalse(report['reconciles'])

    def test_reports_all_four_states_separately(self):
        reg = self._registry(declared_slots=[QF1, prov.mint_slot_id('CUP_KNOCKOUT', 'QF/2')])
        zero = reg.declare_slot(competition='CUP_KNOCKOUT', position='QF/2',
                                provisional_reason='unassigned', first_observed_observation_id=OBS)
        reg.resolve_empty(zero['slot_id'], observation_id=OBS, evidence=EVIDENCE)
        report = prov.reconcile_slots(reg)
        self.assertEqual(report['resolved_empty'], [zero['slot_id']])
        self.assertEqual(report['pending'], [QF1])

    def test_multi_resolved_is_reported_informationally(self):
        reg = self._registry()
        reg.resolve_to_game(QF1, league_game_id=GAME['league_game_id'],
                            home_team_id=GAME['home_team_id'], away_team_id=GAME['away_team_id'],
                            observation_id=OBS, evidence=EVIDENCE)
        reg.resolve_to_game(QF1, league_game_id=GAME2['league_game_id'],
                            home_team_id=GAME2['home_team_id'], away_team_id=GAME2['away_team_id'],
                            observation_id='obs.w1.00000002', evidence=EVIDENCE,
                            role=prov.ROLE_SUCCESSOR)
        report = prov.reconcile_slots(reg)
        self.assertEqual(report['multi_resolved'], [{'slot_id': QF1, 'games': 2}])
        self.assertTrue(report['reconciles'])

    def test_canonical_denominator_is_not_represented_in_the_slot_report(self):
        report = prov.reconcile_slots(self._registry())
        for forbidden in ('canonical', 'postponed', 'cancelled', 'quarantined'):
            self.assertNotIn(forbidden, report)

    def test_assert_slots_lossless_raises_on_a_gap(self):
        with self.assertRaises(prov.ProvisionalError) as ctx:
            prov.assert_slots_lossless(prov.reconcile_slots(self._registry(observed=False)))
        self.assertEqual(ctx.exception.reason, prov.SLOT_ACCOUNTING_GAP)

    def test_reconciliation_is_scoped_by_competition(self):
        reg = self._registry()
        other = reg.declare_slot(competition='REGULAR_ASSIGNMENT_WINDOW', position='1',
                                 anchor='TEAM-BOS', provisional_reason='unassigned',
                                 first_observed_observation_id=OBS)
        reg.declare_expectation(competition='REGULAR_ASSIGNMENT_WINDOW',
                                declared_slots=[other['slot_id']],
                                source_ref='https://synthetic.invalid/announcement',
                                observation_id=OBS, declared_at='2026-11-01T12:00:00Z')
        scoped = prov.reconcile_slots(reg, competition='REGULAR_ASSIGNMENT_WINDOW')
        self.assertEqual(scoped['observed_slots'], [other['slot_id']])
        self.assertNotIn(QF1, scoped['observed_slots'])
        self.assertTrue(scoped['reconciles'])


class FrozenSurfaces(unittest.TestCase):
    def test_no_new_reason_codes_entered_the_package2a_registry(self):
        self.assertEqual(prov.PROVISIONAL_REASONS & ident.QUARANTINE_REASONS, frozenset())

    def test_slot_ids_never_appear_in_the_canonical_denominator(self):
        game = dict(league_game_id='0022600001', home_team_id='TEAM-BOS', away_team_id='TEAM-NYK')
        chain = sch.RevisionChain('0022600001')
        chain.append(sch.schedule_record(game, 'REGULAR_SEASON', None, sch.SCHEDULED,
                                         '2026-12-09T00:00:00Z',
                                         source_updated_at='2026-11-01T00:00:00Z'))
        report = comp.reconcile(1, {'0022600001': chain}, [])
        self.assertNotIn(QF1, json.dumps(report))
        self.assertEqual(report['buckets']['canonical'], 1)

    def test_existing_schedule_digests_are_unchanged_by_m0_use(self):
        game = dict(league_game_id='0022600001', home_team_id='TEAM-BOS', away_team_id='TEAM-NYK')
        record = sch.schedule_record(game, 'REGULAR_SEASON', None, sch.SCHEDULED,
                                     '2026-12-09T00:00:00Z', source_updated_at='2026-11-01T00:00:00Z')
        before = record['revision_digest']
        reg = prov.SlotRegistry()
        reg.declare_slot(competition='CUP_KNOCKOUT', position='QF/1',
                         provisional_reason='unassigned', first_observed_observation_id=OBS)
        reg.resolve_empty(QF1, observation_id=OBS, evidence=EVIDENCE)
        self.assertEqual(sch.digest(record), before)
        self.assertEqual(record['revision_digest'], before)

    def test_as_of_fail_closed_behaviour_is_untouched(self):
        chain = sch.RevisionChain('0022600001')
        game = dict(league_game_id='0022600001', home_team_id='TEAM-BOS', away_team_id='TEAM-NYK')
        chain.append(sch.schedule_record(game, 'REGULAR_SEASON', None, sch.SCHEDULED,
                                         '2026-12-09T00:00:00Z'))   # no source_updated_at
        with self.assertRaises(ident.Quarantine) as ctx:
            chain.as_of('2027-01-01T00:00:00Z')
        self.assertEqual(ctx.exception.reason, ident.REVISION_TIME_UNKNOWN)


if __name__ == '__main__':
    unittest.main()
