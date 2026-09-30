"""B1/B2/B3 draft policies and a single, globally allocated replacement anchor."""
from __future__ import annotations
import copy
import math
import numpy as np
import scipy
from scipy.optimize import linear_sum_assignment
from decision_engine.assignment import RosterSolver
from decision_engine.scoring import IntrinsicModel
from decision_engine.validation import EngineError, canonical_hash

class ReplacementAnchor:
    """B2: unique whole-league allocation, no duplicated flexible-player supply.

    The demand is the explicitly declared ACTIVE/BENCH slot structure for every
    team, not invented position quotas. Bench is its own role. Initial ownership
    is forced to its actual team. Later availability never refits this anchor.
    Unsupported side constraints produce an error, not a relaxed baseline.
    """
    def __init__(self,data,intrinsic=None):
        self.data=copy.deepcopy(data);self.iv=intrinsic or IntrinsicModel(data)
        L=data['league']
        slots=L['roster_slots'];size=sum(s['capacity'] for s in slots)
        if any(s['slot_class'] not in ('ACTIVE','BENCH') or not s['counts_toward_roster_total'] for s in slots):
            self.unsupported('Only fully drafted active and bench storage is supported.')
        for r in L['league_constraints']:
            if r['type']!='TOTAL_ROSTER_COUNT' or set(r['scope_slot_classes'])!={'ACTIVE','BENCH','INJURED_RESERVE','RESERVE'}:
                self.unsupported('B2 allocation needs a supported total-roster rule; position side constraints are not relaxed.')
            if not ((r['operator']=='EQ' and r['limit']==size) or (r['operator']=='LTE' and r['limit']>=size) or (r['operator']=='GTE' and r['limit']<=size)):
                self.unsupported('Declared demand conflicts with total-roster bounds.')
        if any('construction_lock' in e for t in data['roster_state']['teams'] for e in t['entries']):
            self.unsupported('B2 keeper construction locks are not implemented.')
        solver=RosterSolver(data);solver.locks={}
        lp={p['player_id']:p for p in data['league_players']}
        excluded=set(data['draft_state']['manually_excluded_player_ids'])
        self.pool=sorted(p for p in data['reference_model']['normalization_player_ids'] if lp[p]['rosterable'] and p not in excluded)
        self.owner={e['player_id']:t['team_id'] for t in data['roster_state']['teams'] for e in t['entries']}
        if not set(self.owner)<=set(self.pool):self.unsupported('Every keeper must be in the declared B2 anchor pool.')
        teams=sorted(t['team_id'] for t in data['teams'])
        self.demands=[(t,sid,s) for t in teams for sid,s in sorted(solver.slots.items())]
        n=len(self.pool);required=len(self.demands)
        if n<required:self.unsupported('Not enough unique anchor players for declared league demand.')
        options={p:{a.slot_instance_id:a for a in reversed(solver.options(p,locked=False))} for p in self.pool}
        cost=np.full((n,n),np.inf)
        for i,p in enumerate(self.pool):
            for j,(team,sid,slot) in enumerate(self.demands):
                if sid in options[p] and (p not in self.owner or self.owner[p]==team):cost[i,j]=-self.iv.value(p)
            if p not in self.owner:cost[i,required:]=0
        try:rows,cols=linear_sum_assignment(cost)
        except ValueError:self.unsupported('No complete legal global replacement-demand allocation exists.')
        self.allocation=[];self.thresholds={};self.role_eligibility={}
        for i,j in zip(rows,cols):
            if j>=required:continue
            p=self.pool[i];team,sid,slot=self.demands[j]
            role=slot['slot_definition_id']
            entry={'player_id':p,'team_id':team,'slot_instance_id':sid,'role_id':role,'assigned_position_id':options[p][sid].assigned_position_id,'intrinsic_value':self.iv.value(p)}
            self.allocation.append(entry)
            self.thresholds[role]=min(self.thresholds.get(role,math.inf),self.iv.value(p))
        self.allocation.sort(key=lambda a:(a['team_id'],a['slot_instance_id']))
        for p in self.pool:
            self.role_eligibility[p]=sorted({s['slot_definition_id'] for _,sid,s in self.demands if sid in options[p]})
        self.provenance={'method':'B2_GLOBAL_SLOT_ALLOCATION_V1','scipy_version':scipy.__version__,'anchor_pool_player_ids':self.pool,'thresholds_by_slot_role':self.thresholds,'allocation':self.allocation,'bench_treatment':'EXPLICIT_SHARED_BENCH_ROLE','fallback':'UNSUPPORTED_RATHER_THAN_INVENTED_THRESHOLD','tie_rule':'SORTED_ROWS_COLUMNS_DETERMINISTIC_SCIPY_ASSIGNMENT; other optimal allocations may have different thresholds','allocation_objective':math.fsum(a['intrinsic_value'] for a in self.allocation),'thresholds_frozen_at_initial_snapshot':True}
        self.provenance['anchor_sha256']=canonical_hash(self.provenance)

    @staticmethod
    def unsupported(message):
        raise EngineError('B2_UNSUPPORTED_DEMAND',message,status='UNSUPPORTED_CAPABILITY')

    def value(self,p):
        roles=self.role_eligibility.get(p,[])
        if not roles:raise EngineError('B2_UNKNOWN_PLAYER','No declared replacement role for player '+p)
        role=min(roles,key=lambda r:(self.thresholds[r],r))
        return self.iv.value(p)-self.thresholds[role],role


def choose(policy,owned,pool,remaining_after,solver,iv,replacement=None,manager=True):
    feasible=[p for p in sorted(pool) if solver.candidate_reason(owned,p,pool,remaining_after,manager) is None]
    if not feasible:return None
    if policy=='B1':return min(feasible,key=lambda p:(-iv.value(p),p))
    if policy=='B2':
        if replacement is None:raise ValueError('B2 requires an explicit frozen replacement anchor.')
        return min(feasible,key=lambda p:(-replacement.value(p)[0],-iv.value(p),p))
    if policy=='B3':return min(feasible,key=lambda p:(-solver.active_fill(tuple(owned)+(p,),manager),-iv.value(p),p))
    raise ValueError('Unsupported baseline '+policy)
