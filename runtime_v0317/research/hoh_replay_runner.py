from __future__ import annotations
import json, math
from decision_engine.engine import ResearchRunner, uniform
from decision_engine.validation import validate, EngineError
from research.efficient_assignment import EfficientRosterSolver
from research.rule_aware import RuleAwareLineupEvaluator, check_rule_scope
from research.efficient_runner import EfficientRunner

class ReplayEfficientRosterSolver(EfficientRosterSolver):
    """Replay-only initialization from the observed, already-known opponent rosters.

    The user utility never inspects opponent category totals. This avoids solving a
    large anonymous repartition MILP at every historical checkpoint while preserving
    each opponent's legal pre-pick roster state. It is a replay execution shortcut,
    not the default POOL_ONLY implementation.
    """
    def anonymous_rosters(self, pooled_players, agents):
        teams=sorted((t for t in self.data['roster_state']['teams'] if t['team_id']!=self.data['user_team_id']),key=lambda t:t['team_id'])
        agents=sorted(agents,key=lambda a:a['agent_id'])
        if len(teams)!=len(agents):
            return super().anonymous_rosters(pooled_players,agents)
        out={}
        allids=[]
        for agent,team in zip(agents,teams):
            ids=tuple(sorted(e['player_id'] for e in team['entries']))
            if len(ids)!=agent['initial_roster_count'] or not self.league_valid(self._solve(ids,manager=False) or (),False):
                return super().anonymous_rosters(pooled_players,agents)
            out[agent['agent_id']]=ids;allids.extend(ids)
        if sorted(allids)!=sorted(pooled_players):
            return super().anonymous_rosters(pooled_players,agents)
        self.stats['replay_observed_anonymous_initializations']=self.stats.get('replay_observed_anonymous_initializations',0)+1
        return out

class HOHReplayEfficientRunner(EfficientRunner):
    def __init__(self,data,sweeps=0,maximum_storage_patterns=32):
        d=validate(data);check_rule_scope(d)
        ResearchRunner.__init__(self,d,solver_class=ReplayEfficientRosterSolver)
        self.evaluator=RuleAwareLineupEvaluator(self.data,self.solver,sweeps,maximum_storage_patterns)
        self.gumbel_cache={};self.random_stats={'draws_computed':0,'draw_cache_hits':0}
