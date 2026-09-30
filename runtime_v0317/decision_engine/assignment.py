"""Exhaustive construction and terminal-feasibility oracle for small research cases."""
from __future__ import annotations
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable
from .validation import Budget, EngineError

@dataclass(frozen=True)
class Placement:
    player_id: str
    slot_instance_id: str
    assigned_position_id: str
    slot_class: str
    counts_toward_roster_total: bool
    def output(self):
        return {'player_id':self.player_id,'slot_instance_id':self.slot_instance_id,'assigned_position_id':self.assigned_position_id}

def violation(value: int, operator: str, target: int) -> int:
    if operator=='LTE':return max(0,value-target)
    if operator=='GTE':return max(0,target-value)
    return abs(value-target)

class RosterSolver:
    def __init__(self,data: dict):
        self.data=data
        self.lp={p['player_id']:p for p in data['league_players']}
        self.positions=sorted(p['position_id'] for p in data['league']['positions'] if p['rosterable'])
        self.slots={f'{s["slot_definition_id"]}#{i+1}':s for s in data['league']['roster_slots'] for i in range(s['capacity'])}
        self.rules=sorted(data['league']['league_constraints'],key=lambda r:r['constraint_id'])
        self.policies=sorted(data['manager_profile']['position_policies'],key=lambda r:r['policy_id'])
        self.lambda_=data['methodology_config']['soft_policy_lambda']
        self.maxnodes=data['methodology_config']['max_search_nodes']
        self.locks={e['player_id']:e['construction_lock'] for t in data['roster_state']['teams'] if t['team_id']==data['user_team_id'] for e in t['entries'] if 'construction_lock' in e}
        self.inherited: dict[str,int]={}
        self.cache: dict[tuple,list[tuple[Placement,...]]]={}
        self.feas_cache={}

    def options(self,pid: str, locked: bool=True) -> list[Placement]:
        p=self.lp[pid]
        if not p['rosterable']:return []
        result=[]
        for sid,s in sorted(self.slots.items()):
            tags=s['required_status_tags_any_of']
            if tags and not set(tags)&set(p['status_tags']):continue
            positions=set(p['eligibility']['position_ids'])
            if s['eligibility']['mode']=='POSITION_SET':positions &= set(s['eligibility']['position_ids'])
            for position in sorted(positions):
                if locked and pid in self.locks:
                    lock=self.locks[pid]
                    if (sid,position)!=(lock['slot_instance_id'],lock['assigned_position_id']):continue
                result.append(Placement(pid,sid,position,s['slot_class'],s['counts_toward_roster_total']))
        return result

    def count(self,assignment: tuple[Placement,...],rule: dict) -> int:
        selected=[a for a in assignment if a.slot_class in rule['scope_slot_classes']]
        if rule.get('type')=='TOTAL_ROSTER_COUNT':return sum(a.counts_toward_roster_total for a in selected)
        target=set(rule['position_ids']);basis=rule['counting_basis']
        if basis=='ASSIGNED_POSITION':return sum(a.assigned_position_id in target for a in selected)
        if basis=='ANY_ELIGIBILITY':return sum(bool(target&set(self.lp[a.player_id]['eligibility']['position_ids'])) for a in selected)
        return sum(self.lp[a.player_id]['eligibility']['primary_position_id'] in target for a in selected)

    def hard_vector(self,assignment,terminal=False,baseline=False):
        result={}
        for p in self.policies:
            n=self.count(assignment,p)
            for bound,op in [('minimum','GTE'),('maximum','LTE')]:
                b=p.get(bound)
                if b and b['enforcement']=='HARD':
                    active=terminal or b['evaluation_phase']=='AT_ALL_TIMES' or (baseline and bound=='maximum')
                    if active:result[p['policy_id']+'/'+bound]=violation(n,op,b['value'])
        return result

    def league_valid(self,assignment,terminal=False):
        if terminal:
            used={a.slot_instance_id for a in assignment}
            if any(s['required_at_completion'] and sid not in used for sid,s in self.slots.items()):return False
        return all(not violation(self.count(assignment,r),r['operator'],r['limit']) for r in self.rules if terminal or r['evaluation_phase']=='AT_ALL_TIMES')

    def manager_valid(self,assignment,terminal=False):
        return all(v<=self.inherited.get(k,0) for k,v in self.hard_vector(assignment,terminal).items())

    def soft_penalty(self,assignment):
        total=0.0;effects=[]
        for p in self.policies:
            n=self.count(assignment,p);deviations=[]
            if 'target' in p:deviations.append(abs(n-p['target']['value'])*p['target']['weight'])
            for bound,op in [('minimum','GTE'),('maximum','LTE')]:
                b=p.get(bound)
                if b and b['enforcement']=='SOFT':deviations.append(violation(n,op,b['value'])*b['weight'])
            units=max(deviations,default=0.0)
            total+=units
            if deviations:effects.append({'policy_id':p['policy_id'],'assigned_count':n,'weighted_deviation':units,'penalty':self.lambda_*units})
        return self.lambda_*total,effects

    def penalty_upper_bound(self):
        maximum=len(self.slots);total=0
        for p in self.policies:
            bounds=[]
            if 'target' in p:bounds.append(max(p['target']['value'],abs(maximum-p['target']['value']))*p['target']['weight'])
            for name in ('minimum','maximum'):
                b=p.get(name)
                if b and b['enforcement']=='SOFT':bounds.append(max(b['value'],abs(maximum-b['value']))*b['weight'])
            total+=max(bounds,default=0)
        return self.lambda_*total

    @staticmethod
    def key(assignment):
        return tuple((a.player_id,a.slot_instance_id,a.assigned_position_id) for a in sorted(assignment,key=lambda a:a.player_id))

    def all(self,players: Iterable[str], terminal: bool=False, manager: bool=True, budget: Budget|None=None) -> list[tuple[Placement,...]]:
        ids=tuple(sorted(players));cachekey=(ids,terminal,manager,tuple(sorted(self.inherited.items())))
        if len(ids)!=len(set(ids)):return []
        if cachekey in self.cache:return self.cache[cachekey]
        if len(ids)>len(self.slots):return []
        budget=budget or Budget(self.maxnodes,'construction assignment')
        opts={p:self.options(p,locked=manager) for p in ids}
        if any(not o for o in opts.values()):return []
        order=sorted(ids,key=lambda p:(len(opts[p]),p));out=[]
        def walk(i,used,current):
            budget.tick()
            if i==len(order):
                assignment=tuple(sorted(current,key=lambda a:a.player_id))
                if self.league_valid(assignment,terminal) and (not manager or self.manager_valid(assignment,terminal)):
                    out.append(assignment)
                return
            pid=order[i]
            for a in opts[pid]:
                if a.slot_instance_id not in used:walk(i+1,used|{a.slot_instance_id},current+[a])
        walk(0,set(),[])
        out.sort(key=self.key);self.cache[cachekey]=out
        return out

    def initialize(self,players):
        current=self.all(players,manager=False)
        # Manager locks must be respected in the observed pre-pick construction, too.
        current=[a for a in current if all(p.player_id not in self.locks or (p.slot_instance_id,p.assigned_position_id)==(self.locks[p.player_id]['slot_instance_id'],self.locks[p.player_id]['assigned_position_id']) for p in a)]
        if not current:raise EngineError('ILLEGAL_LEAGUE_ROSTER','The current roster has no league-legal storage assignment.')
        def key(a):
            v=self.hard_vector(a,baseline=True)
            return (sum(v.values()),tuple(v[k] for k in sorted(v)),self.key(a))
        anchor=min(current,key=key)
        self.inherited=self.hard_vector(anchor,baseline=True)
        self.cache.clear();self.feas_cache.clear()
        return anchor

    def best(self,players,terminal=False,manager=True):
        possibilities=self.all(players,terminal,manager)
        if not possibilities:return None
        return min(possibilities,key=lambda a:((self.soft_penalty(a)[0] if manager else 0),self.key(a)))

    def active_fill(self,players,manager=True):
        possibilities=self.all(players,False,manager)
        return max((sum(a.slot_class=='ACTIVE' for a in assignment) for assignment in possibilities),default=-1)

    def can_complete(self,owned,available,remaining_picks,manager=True):
        """Existential completion with distinct actual available players and exact remaining selections."""
        ids=tuple(sorted(owned));pool=tuple(sorted(set(available)-set(owned)))
        key=(ids,pool,remaining_picks,manager,tuple(sorted(self.inherited.items())))
        if key in self.feas_cache:return self.feas_cache[key]
        if remaining_picks<0 or remaining_picks>len(pool) or len(ids)+remaining_picks>len(self.slots):return False
        budget=Budget(self.maxnodes,'actual-pool completion')
        if not self.all(ids,False,manager,budget):return False
        result=False
        for addition in combinations(pool,remaining_picks):
            budget.tick()
            if self.all(ids+addition,True,manager,budget):result=True;break
        self.feas_cache[key]=result
        return result

    def candidate_reason(self,owned,candidate,pool,picks_after,manager=True):
        next_roster=tuple(owned)+(candidate,)
        if not self.all(next_roster,False,False):return 'NO_LEGAL_ROSTER_ASSIGNMENT'
        if manager and not self.all(next_roster,False,True):return 'MANAGER_HARD_CONSTRAINT'
        if not self.can_complete(next_roster,set(pool)-{candidate},picks_after,manager):return 'NO_FEASIBLE_COMPLETION'
        return None

    def anonymous_rosters(self,pooled_players,agents):
        """Canonical repartition of pooled non-user ownership; never reads named team portfolios."""
        ordered=sorted(agents,key=lambda a:a['agent_id']);pool=tuple(sorted(pooled_players));budget=Budget(self.maxnodes,'anonymous ownership partition')
        def walk(i,remaining,out):
            budget.tick()
            if i==len(ordered):return out if not remaining else None
            a=ordered[i]
            for group in combinations(remaining,a['initial_roster_count']):
                if self.all(group,False,False,budget):
                    result=walk(i+1,tuple(p for p in remaining if p not in group),{**out,a['agent_id']:group})
                    if result is not None:return result
            return None
        result=walk(0,pool,{})
        if result is None:raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE','Cannot form league-legal anonymous demand states from pooled unavailable ownership.')
        return result
