"""Exact matching fast paths, with the exhaustive oracle retained for richer rules.

Only feasibility/active-fill are accelerated. Nonlinear lineup utility is NOT
replaced by a linear objective. No position policy/cap is silently relaxed.
"""
from __future__ import annotations
import numpy as np
from scipy.optimize import linear_sum_assignment
from .assignment import RosterSolver

class MatchingRosterSolver(RosterSolver):
    def __init__(self, data):
        super().__init__(data)
        self.fast_calls = 0
        self.oracle_calls = 0

    def fast_supported(self, manager=True):
        if any(r['type'] != 'TOTAL_ROSTER_COUNT' for r in self.rules):
            return False
        if any(not s['counts_toward_roster_total'] for s in self.slots.values()):
            return False
        if manager and any(b in p and p[b]['enforcement']=='HARD'
                           for p in self.policies for b in ('minimum','maximum')):
            return False
        return True

    def matching_witness(self, owned, available, remaining, terminal=True, manager=True):
        """Fill K distinct real players, all owned, plus exactly S-K empty slots.

        Owned rows cannot use rejection columns; empty dummy rows cannot occupy
        completion-required slots. Square full matching therefore proves all
        cardinality and coverage constraints at once, without big-M coefficients.
        """
        owned=tuple(sorted(owned));pool=tuple(sorted(set(available)-set(owned)))
        k=len(owned)+remaining;slotids=sorted(self.slots);s=len(slotids)
        if len(owned)!=len(set(owned)) or remaining<0 or remaining>len(pool) or k>s:
            return None
        ids=owned+pool;n=len(ids);empty=s-k;reject=n-k
        dim=n+empty
        if dim==0:
            return () if self.league_valid((),terminal) else None
        if dim>s+reject or reject<0:return None
        costs=np.full((dim,dim),np.inf)
        options={p:{a.slot_instance_id:a for a in reversed(self.options(p,locked=manager))} for p in ids}
        for i,p in enumerate(ids):
            for j,sid in enumerate(slotids):
                if sid in options[p]:costs[i,j]=0.0
            if p not in owned and reject:costs[i,s:]=0.0
        for i in range(n,dim):
            for j,sid in enumerate(slotids):
                if not terminal or not self.slots[sid]['required_at_completion']:
                    costs[i,j]=0.0
        try:r,c=linear_sum_assignment(costs)
        except ValueError:return None
        answer=tuple(sorted((options[ids[i]][slotids[j]] for i,j in zip(r,c) if i<n and j<s),key=lambda a:a.player_id))
        if len(answer)!=k or not set(owned)<={a.player_id for a in answer}:return None
        if not self.league_valid(answer,terminal) or manager and not self.manager_valid(answer,terminal):return None
        return answer

    def can_complete(self,owned,available,remaining_picks,manager=True):
        if not self.fast_supported(manager):
            self.oracle_calls+=1
            return super().can_complete(owned,available,remaining_picks,manager)
        key=(tuple(sorted(owned)),tuple(sorted(set(available)-set(owned))),remaining_picks,manager,tuple(sorted(self.inherited.items())))
        if key not in self.feas_cache:
            self.fast_calls+=1
            current=self.matching_witness(owned,[],0,terminal=False,manager=manager)
            self.feas_cache[key]=(current is not None and self.matching_witness(owned,available,remaining_picks,manager=manager) is not None)
        return self.feas_cache[key]

    def active_fill(self,players,manager=True):
        if not self.fast_supported(manager):
            self.oracle_calls+=1
            return super().active_fill(players,manager)
        self.fast_calls+=1
        ids=sorted(players);slots=sorted(self.slots)
        if len(set(ids))!=len(ids) or len(ids)>len(slots):return -1
        if not ids:return 0 if self.league_valid((),False) else -1
        opts={p:{a.slot_instance_id:a for a in reversed(self.options(p,locked=manager))} for p in ids}
        cost=np.full((len(ids),len(slots)),np.inf)
        for i,p in enumerate(ids):
            for j,sid in enumerate(slots):
                if sid in opts[p]:cost[i,j]=-float(self.slots[sid]['slot_class']=='ACTIVE')
        try:rows,cols=linear_sum_assignment(cost)
        except ValueError:return -1
        assignment=tuple(opts[ids[i]][slots[j]] for i,j in zip(rows,cols))
        if not self.league_valid(assignment,False):return -1
        return sum(a.slot_class=='ACTIVE' for a in assignment)

    def candidate_reason(self,owned,candidate,pool,picks_after,manager=True):
        if not self.fast_supported(manager):
            self.oracle_calls+=1
            return super().candidate_reason(owned,candidate,pool,picks_after,manager)
        current=tuple(owned)+(candidate,)
        if self.matching_witness(current,[],0,False,manager) is None:
            return 'NO_LEGAL_ROSTER_ASSIGNMENT'
        if not self.can_complete(current,set(pool)-{candidate},picks_after,manager):
            return 'NO_FEASIBLE_COMPLETION'
        return None
