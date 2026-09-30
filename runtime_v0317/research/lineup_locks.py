"""Offline scheduled-game-time lineup transition guard, not a live integration.

Checks a proposed mutation against a supplied prior full lineup and schedule.
Does not authenticate prior lineups, change projected outcomes, activate injured
players automatically, or impose manager construction positions on live lineups.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from decision_engine.validation import EngineError,validate,canonical_hash,timestamp
from research.constrained_assignment import ConstrainedRosterSolver

SCHEMA=json.loads(Path(__file__).with_name('lineup-transition.schema.json').read_text())
VALIDATOR=Draft202012Validator(SCHEMA,format_checker=FormatChecker())


def validate_lineup_transition(snapshot,context):
    """Return an immutable transition record, or raise a structured EngineError."""
    d=validate(snapshot)
    errors=sorted(VALIDATOR.iter_errors(context),key=lambda e:(str(list(e.path)),e.message))
    if errors:raise EngineError('LINEUP_TRANSITION_SCHEMA',errors[0].message)
    c=copy.deepcopy(context)
    if c['snapshot_sha256']!=canonical_hash(d):raise EngineError('STALE_LINEUP_SNAPSHOT','The supplied snapshot hash differs from the declared lock context.')
    now=timestamp(c['decision_at']);start=timestamp(c['period_starts_at']);end=timestamp(c['period_ends_at'])
    if not start<=now<end:raise EngineError('LOCK_PERIOD_INVALID','Decision time must be inside the explicit half-open lineup period.')
    if timestamp(c['previous_committed_at'])>now or timestamp(c['schedule_as_of'])>now or timestamp(d['created_at'])>now:
        raise EngineError('FUTURE_LOCK_INFORMATION','The prior state/schedule/snapshot cannot be dated after the decision.')
    if c['lock_basis']!='PLAYER_SCHEDULED_GAME_TIME':raise EngineError('LOCK_BASIS_UNSUPPORTED','This guard implements supplied scheduled-game-time locks only.',status='UNSUPPORTED_CAPABILITY')
    if d['league']['lineup_rules']['lock_mode']!='DAILY':raise EngineError('LOCK_MODE_UNSUPPORTED','The transition guard currently supports daily per-player locks.',status='UNSUPPORTED_CAPABILITY')
    periods={p['period_id']:p for p in d['exposure_model']['periods']}
    if c['period_id'] not in periods or not start<=timestamp(periods[c['period_id']]['starts_at'])<end:
        raise EngineError('LOCK_PERIOD_MISMATCH','The transition period must contain the referenced forecast period.')
    owned=sorted(e['player_id'] for t in d['roster_state']['teams'] if t['team_id']==d['user_team_id'] for e in t['entries'])
    if set(c['scheduled_starts'])!=set(owned):raise EngineError('LOCK_SCHEDULE_COVERAGE','Provide an explicit schedule list, including off-day empty lists, for each owned player.')
    starts={}
    for p,items in c['scheduled_starts'].items():
        times=sorted(timestamp(x) for x in items)
        if len(set(times))!=len(times) or any(not start<=t<end for t in times):raise EngineError('LOCK_SCHEDULE_INVALID','Duplicate starts or games outside this lineup period.')
        starts[p]=times
    solver=ConstrainedRosterSolver(d)
    def parse_plan(rows):
        if len(rows)!=len(owned) or {r['player_id'] for r in rows}!=set(owned):raise EngineError('LINEUP_OWNERSHIP_CHANGED','Lineup moves cannot add/drop/duplicate owned players.')
        if len({r['slot_instance_id'] for r in rows})!=len(rows):raise EngineError('DUPLICATE_LINEUP_SLOT','A slot cannot contain two players.')
        out={}
        for r in rows:
            possibilities=[a for a in solver.options(r['player_id'],False) if a.output()==r]
            if not possibilities:raise EngineError('ILLEGAL_LINEUP_PLACEMENT','Slot eligibility or supplied status does not permit this placement.')
            out[r['player_id']]=possibilities[0]
        # Roster counting is separate from active lineup position. Preserve storage
        # class, then solve for a legal counting assignment across ACTIVE/BENCH.
        checker=ConstrainedRosterSolver(d)
        for p,placement in out.items():
            allowed=([a for a in checker.options(p,False) if a.slot_class in ('ACTIVE','BENCH')]
                     if placement.slot_class in ('ACTIVE','BENCH') else
                     [a for a in checker.options(p,False) if a.slot_instance_id==placement.slot_instance_id])
            checker.option_cache[(p,False)]=allowed
        if checker._solve(owned,manager=False) is None:
            raise EngineError('LINEUP_STORAGE_RULE_VIOLATION','The proposed storage classes cannot satisfy league roster counts.')
        return out
    before=parse_plan(c['before_plan']);after=parse_plan(c['after_plan'])
    sticky=set(c['previously_locked_player_ids'])
    if not sticky<=set(owned):raise EngineError('UNKNOWN_STICKY_LOCK','Previously locked player is not owned.')
    locked=sorted(sticky|{p for p in owned if before[p].slot_class in c['lock_slot_classes'] and starts[p] and min(starts[p])<=now})
    changed=sorted(p for p in owned if before[p].output()!=after[p].output())
    violations=sorted(set(locked)&set(changed))
    if violations:raise EngineError('PLAYER_ALREADY_LOCKED','Started players cannot change position or storage: '+', '.join(violations))
    return {'schema_version':'decision-engine-lineup-transition-result/0.3.6',
            'status':'LEGAL_TRANSITION','decision_at':c['decision_at'],'period_id':c['period_id'],
            'locked_player_ids':locked,'changed_player_ids':changed,
            'committed_plan':[after[p].output() for p in owned],
            'plan_sha256':canonical_hash([after[p].output() for p in owned]),
            'context_sha256':canonical_hash(c),'snapshot_sha256':canonical_hash(d),
            'boundary':'SUPPLIED_SNAPSHOT_TRANSITION_ONLY_NOT_LIVE_AUTHENTICATION',
            'manager_construction_positions_imposed_on_lineup':False}


def check_lineup_transition(snapshot,context):
    try:return validate_lineup_transition(snapshot,context)
    except EngineError as e:return {'status':'REJECTED','run_status':e.status,'messages':[e.as_dict()]}
