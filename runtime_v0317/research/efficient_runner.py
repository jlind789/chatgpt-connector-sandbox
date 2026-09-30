"""Opt-in v0.3.7 typed-legality execution. Recommendation mathematics unchanged."""
from __future__ import annotations
import json
import math
import copy
from decision_engine.engine import ResearchRunner, empty_result, uniform
from decision_engine.validation import validate, EngineError
from research.rule_aware import RuleAwareRunner, RuleAwareLineupEvaluator, check_rule_scope
from research.efficient_assignment import EfficientRosterSolver

PROFILE = 'TYPED_CONSTRAINED_ROSTER_BOUNDED_LINEUP_V037'

class EfficientRunner(RuleAwareRunner):
    def __init__(self, data, sweeps=2, maximum_storage_patterns=128):
        d = validate(data)
        check_rule_scope(d)
        ResearchRunner.__init__(self, d, solver_class=EfficientRosterSolver)
        self.evaluator = RuleAwareLineupEvaluator(self.data, self.solver, sweeps, maximum_storage_patterns)
        self.gumbel_cache = {}
        self.random_stats = {"draws_computed": 0, "draw_cache_hits": 0}

    def _log_gumbel(self, rollout, pick_id, player_id):
        # Cache only the existing keyed noise, not candidate/pool-dependent scores.
        key = (self.cfg['seed'], rollout, pick_id, player_id)
        if key not in self.gumbel_cache:
            u = min(max(uniform(*key), 1e-16), 1-1e-16)
            self.gumbel_cache[key] = math.log(-math.log(u))
            self.random_stats['draws_computed'] += 1
        else:
            self.random_stats['draw_cache_hits'] += 1
        return self.gumbel_cache[key]

    def path(self, first, rollout, continuation):
        # Same loop/arithmetic as ScalableRunner.path; only keyed-noise reuse is new.
        owned = list(self.owned) + [first]
        pool = set(self.available) - {first}
        anonymous = {k: list(v) for k, v in self.anonymous.items()}
        trace = [{'pick_id': self.picks[0]['pick_id'], 'actor_id': 'USER', 'player_id': first}]
        last = max(i for i, p in enumerate(self.picks) if p['actor_id'] == 'USER')
        def failure(reason):
            return {'decision_utility': self.failure_value, 'league_utility': 0.,
                    'strategy_utility': 0., 'soft_policy_penalty': 0., 'failed': True,
                    'failure_reason': reason, 'trace': trace, 'owned_player_ids': sorted(owned),
                    'category_results': [], 'expected_usable_stats': {}, 'evaluation': None}
        for i, pick in enumerate(self.picks[1:last+1], 1):
            actor = pick['actor_id']
            later = sum(p['actor_id'] == actor for p in self.picks[i+1:])
            if actor == 'USER':
                chosen = self.choose(continuation, owned, pool, later)
                if chosen is None:
                    return failure('USER_NO_FEASIBLE_CONTINUATION')
                owned.append(chosen)
            else:
                minimum = min((self.ranks[p] for p in pool), default=0)
                def priority(p):
                    return (-(self.ranks[p] - minimum) / self.cfg['neutral_temperature']
                            - self._log_gumbel(rollout, pick['pick_id'], p), p)
                ordered = sorted(pool, key=priority, reverse=True)
                chosen = next((p for p in ordered if self.solver.candidate_reason(
                    anonymous[actor], p, pool, later, False) is None), None)
                if chosen is None:
                    raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE_PATH',
                                      'Anonymous demand cannot complete a legal selection.',
                                      status='UNSUPPORTED_CAPABILITY')
                anonymous[actor].append(chosen)
            pool.remove(chosen)
            trace.append({'pick_id': pick['pick_id'], 'actor_id': actor, 'player_id': chosen})
        evaluated = self.evaluator.evaluate(owned, terminal=True)
        if evaluated is None:
            return failure('NO_VALID_TERMINAL_LINEUP_OR_ROSTER')
        return {k: v for k, v in evaluated.items() if k in (
            'decision_utility', 'league_utility', 'strategy_utility', 'soft_policy_penalty',
            'category_results', 'expected_usable_stats')} | {
                'failed': False, 'failure_reason': None, 'trace': trace,
                'owned_player_ids': sorted(owned), 'evaluation': evaluated}

    def run(self):
        result = super().run()
        result['provenance'].update(
            execution_profile=PROFILE, capability_profile=PROFILE,
            construction_solver='TYPED_INTEGER_LEGALITY_WITH_EXPANDED_VALIDATED_WITNESSES',
            legality_cache_scope='ONE_IMMUTABLE_NORMALIZED_SNAPSHOT',
            unchanged_reference_profile='CONSTRAINED_ROSTER_BOUNDED_LINEUP_V036',
            performance_change_only=True,
            solver_statistics=dict(self.solver.stats),
            random_cache_statistics=dict(self.random_stats))
        return result


def recommend_efficient(data, sweeps=2, maximum_storage_patterns=128):
    try:
        result = EfficientRunner(data, sweeps, maximum_storage_patterns).run()
        json.dumps(result, allow_nan=False)
        return result
    except EngineError as e:
        result = empty_result(data)
        result['run_status'] = e.status
        result['messages'] = [e.as_dict()]
        return result


def apply_user_pick_efficient(data, player_id):
    """Pure transition using the same exact legality guard; never mutates input."""
    runner = EfficientRunner(data)
    if not runner.picks or runner.picks[0]['actor_id'] != 'USER':
        raise EngineError('USER_NOT_ON_CLOCK', 'User must be on clock.')
    if player_id not in runner.available:
        raise EngineError('PLAYER_UNAVAILABLE', 'Player is not available.')
    remaining = sum(p['actor_id'] == 'USER' for p in runner.picks) - 1
    reason = runner.solver.candidate_reason(runner.owned, player_id, runner.available, remaining)
    if reason:
        raise EngineError(reason, 'Pick violates roster or actual-pool completion requirements.')
    result = copy.deepcopy(runner.data)
    next(t for t in result['roster_state']['teams'] if t['team_id'] == result['user_team_id'])['entries'].append(
        {'player_id': player_id, 'acquisition_type': 'DRAFT_PICK'})
    result['draft_state']['remaining_picks'].pop(0)
    result['draft_state']['status'] = 'IN_PROGRESS' if result['draft_state']['remaining_picks'] else 'COMPLETE'
    result['snapshot_id'] += '-after-' + player_id
    return result
