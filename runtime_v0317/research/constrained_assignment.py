"""Exact integer construction/completion; bounded lineup quality stays separate.

The model has a binary variable for each (player, slot, assigned position).
Count bases, rule phases, reserve status gates and grandfathered hard-policy
vectors use the same semantics as the small exhaustive reference solver.
A limit or unverified numerical solution is NOT reported as infeasibility.
"""
from __future__ import annotations
import math
from itertools import combinations
from collections import defaultdict
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import csc_array
from decision_engine.assignment import RosterSolver, violation
from decision_engine.validation import EngineError


class IntegerModel:
    """Sparse bounded MILP builder with strict certificate/solution validation."""
    def __init__(self, node_limit=500000):
        self.lb=[]; self.ub=[]; self.integral=[]; self.cost=[]; self.rows=[]
        self.node_limit=node_limit; self.solves=0
    def var(self,low=0.,high=1.,integer=True,cost=0.):
        j=len(self.lb);self.lb.append(low);self.ub.append(high)
        self.integral.append(int(integer));self.cost.append(float(cost));return j
    def row(self,coeff,low=-np.inf,high=np.inf):
        self.rows.append(({j:float(v) for j,v in coeff.items() if v},float(low),float(high)))
    def optimize(self,objective=None):
        n=len(self.lb)
        if n==0:
            return np.zeros(0) if all(lo<=0<=hi for _,lo,hi in self.rows) else None
        rr=[];cc=[];vv=[]
        for i,(row,_,_) in enumerate(self.rows):
            for j,v in row.items():rr.append(i);cc.append(j);vv.append(v)
        A=csc_array((vv,(rr,cc)),shape=(len(self.rows),n))
        cost=np.asarray(self.cost if objective is None else [objective.get(j,0.) for j in range(n)],float)
        self.solves+=1
        result=milp(cost,integrality=np.asarray(self.integral),bounds=Bounds(self.lb,self.ub),
                    constraints=LinearConstraint(A,[x[1] for x in self.rows],[x[2] for x in self.rows]),
                    options={'node_limit':self.node_limit,'mip_rel_gap':0.,'presolve':True})
        if result.status==2:return None
        if result.status==1:
            raise EngineError('CONSTRAINED_SOLVER_LIMIT','Integer solver reached its deterministic node limit; no infeasibility or partial ranking is claimed.',status='RESOURCE_LIMIT')
        if result.status!=0 or result.x is None:
            raise EngineError('CONSTRAINED_SOLVER_ERROR',str(result.message),status='UNSUPPORTED_CAPABILITY')
        x=np.asarray(result.x,float)
        if not np.isfinite(x).all():raise EngineError('CONSTRAINED_NUMERICAL_ERROR','Nonfinite solver output.')
        # Integral certificates are checked before conversion, never rounding an LP relaxation.
        ints=np.asarray(self.integral,dtype=bool)
        if np.any(np.abs(x[ints]-np.rint(x[ints]))>1e-6):
            raise EngineError('CONSTRAINED_NUMERICAL_ERROR','Solver returned a nonintegral assignment.')
        x[ints]=np.rint(x[ints]);values=np.asarray(A@x)
        if np.any(x<np.asarray(self.lb)-1e-7) or np.any(x>np.asarray(self.ub)+1e-7) or any(
                val<lo-1e-7 or val>hi+1e-7 for val,(_,lo,hi) in zip(values,self.rows)):
            raise EngineError('CONSTRAINED_NUMERICAL_ERROR','Integer witness violates modeled constraints.')
        return x


class ConstrainedRosterSolver(RosterSolver):
    """One certified witness per query, not enumeration of every assignment.

    `all` preserves the caller's feasibility protocol but returns at most one
    witness. Use `storage_options` for joint storage/lineup optimization.
    """
    def __init__(self,data):
        super().__init__(data)
        self.option_cache={};self.query_cache={};self.storage_cache={}
        self.stats={'milp_queries':0,'cache_hits':0,'storage_patterns':0}
    def options(self,pid,locked=True):
        key=(pid,locked)
        if key not in self.option_cache:self.option_cache[key]=super().options(pid,locked)
        return self.option_cache[key]
    def coefficient(self,a,rule):
        return self.count((a,),rule)
    @staticmethod
    def _bound(model,coeff,operator,value,slack=None):
        if operator in ('GTE','EQ'):
            row=dict(coeff)
            if slack is not None:row[slack]=1.
            model.row(row,low=value)
        if operator in ('LTE','EQ'):
            row=dict(coeff)
            if slack is not None:row[slack]=-1.
            model.row(row,high=value)
    def _model(self,owned,pool,remaining,terminal,manager,mode='FEASIBLE',storage=None):
        owned=tuple(sorted(owned));pool=tuple(sorted(set(pool)-set(owned)))
        if len(set(owned))!=len(owned) or remaining<0 or remaining>len(pool) or len(owned)+remaining>len(self.slots):return None
        model=IntegerModel(self.maxnodes);edges=[];byplayer=defaultdict(dict);byslot=defaultdict(dict)
        ids=sorted(set(owned)|set(pool));ownedset=set(owned)
        for p in ids:
            for a in self.options(p,locked=manager):
                if storage is not None and ((p in storage)!=(a.slot_class not in ('ACTIVE','BENCH'))):continue
                j=model.var();edges.append((j,a));byplayer[p][j]=1.;byslot[a.slot_instance_id][j]=1.
        for p in ids:model.row(byplayer[p],low=1. if p in ownedset else 0.,high=1.)
        for sid,s in sorted(self.slots.items()):model.row(byslot[sid],low=1. if terminal and s['required_at_completion'] else 0.,high=1.)
        model.row({j:1. for j,a in edges},low=len(owned)+remaining,high=len(owned)+remaining)
        for rule in self.rules:
            if terminal or rule['evaluation_phase']=='AT_ALL_TIMES':
                coeff={j:self.coefficient(a,rule) for j,a in edges}
                self._bound(model,coeff,rule['operator'],rule['limit'])
        slack_ids={};soft_ids=[]
        if manager:
            for p in self.policies:
                coeff={j:self.coefficient(a,p) for j,a in edges}
                for bound,op in [('minimum','GTE'),('maximum','LTE')]:
                    b=p.get(bound)
                    if not b or b['enforcement']!='HARD':continue
                    active=terminal or b['evaluation_phase']=='AT_ALL_TIMES' or (mode=='BASELINE' and bound=='maximum')
                    if not active:continue
                    key=p['policy_id']+'/'+bound
                    if mode=='BASELINE':
                        slack=model.var(high=np.inf,integer=True);slack_ids[key]=slack
                        self._bound(model,coeff,op,b['value'],slack)
                    else:
                        inherited=self.inherited.get(key,0)
                        adjusted=b['value']+inherited if op=='LTE' else b['value']-inherited
                        self._bound(model,coeff,op,adjusted)
                if mode=='SOFT':
                    terms=[]
                    if 'target' in p:terms.append(('EQ',p['target']['value'],p['target']['weight']))
                    for bound,op in [('minimum','GTE'),('maximum','LTE')]:
                        b=p.get(bound)
                        if b and b['enforcement']=='SOFT':terms.append((op,b['value'],b['weight']))
                    if terms:
                        z=model.var(high=np.inf,integer=False);soft_ids.append(z)
                        for op,target,weight in terms:
                            self._bound(model,{j:v*weight for j,v in coeff.items()},op,target*weight,z)
        return model,edges,slack_ids,soft_ids
    def _solve(self,owned,pool=(),remaining=0,terminal=False,manager=True,mode='FEASIBLE',storage=None):
        key=(tuple(sorted(owned)),tuple(sorted(set(pool)-set(owned))),remaining,terminal,manager,mode,
             None if storage is None else tuple(sorted(storage)),tuple(sorted(self.inherited.items())))
        if key in self.query_cache:self.stats['cache_hits']+=1;return self.query_cache[key]
        built=self._model(owned,pool,remaining,terminal,manager,mode,storage)
        if built is None:self.query_cache[key]=None;return None
        model,edges,slacks,soft=built;self.stats['milp_queries']+=1
        if mode=='BASELINE':
            x=model.optimize({j:1. for j in slacks.values()})
            if x is not None and slacks:
                total=int(round(sum(x[j] for j in slacks.values())))
                model.row({j:1. for j in slacks.values()},low=total,high=total)
                # Match the exact oracle's no-trading-of-existing-overages anchor.
                for name,j in sorted(slacks.items()):
                    x=model.optimize({j:1.})
                    if x is None:raise EngineError('CONSTRAINED_NUMERICAL_ERROR','Lost feasible baseline during lexicographic solve.')
                    value=int(round(x[j]));model.row({j:1.},low=value,high=value)
        elif mode=='SOFT':x=model.optimize({j:1. for j in soft})
        elif mode=='ACTIVE':x=model.optimize({j:-float(a.slot_class=='ACTIVE') for j,a in edges})
        else:x=model.optimize({})
        if x is None:self.query_cache[key]=None;return None
        assignment=tuple(sorted((a for j,a in edges if x[j]>.5),key=lambda a:a.player_id))
        selected=[a.player_id for a in assignment];used=[a.slot_instance_id for a in assignment]
        if len(selected)!=len(owned)+remaining or len(set(selected))!=len(selected) or len(set(used))!=len(used) or not set(owned)<=set(selected) or not self.league_valid(assignment,terminal):
            raise EngineError('CONSTRAINED_WITNESS_INVALID','Independent roster validation rejected integer solution.')
        if manager and mode!='BASELINE' and not self.manager_valid(assignment,terminal):
            raise EngineError('CONSTRAINED_WITNESS_INVALID','Hard manager policies rejected integer solution.')
        if manager and any(a not in self.options(a.player_id,True) for a in assignment):
            raise EngineError('CONSTRAINED_WITNESS_INVALID','Construction lock or status gate violated.')
        if mode=='SOFT':
            calculated=self.soft_penalty(assignment)[0]/self.lambda_ if self.lambda_ else 0.
            if self.lambda_ and not math.isclose(calculated,sum(x[j] for j in soft),rel_tol=1e-7,abs_tol=1e-7):
                raise EngineError('CONSTRAINED_WITNESS_INVALID','Soft penalty ledger does not match optimized assignment.')
        self.query_cache[key]=assignment;return assignment
    def all(self,players,terminal=False,manager=True,budget=None):
        a=self._solve(players,terminal=terminal,manager=manager)
        return [] if a is None else [a]
    def initialize(self,players):
        a=self._solve(players,manager=True,mode='BASELINE')
        if a is None:raise EngineError('ILLEGAL_LEAGUE_ROSTER','No league-legal current assignment including storage locks.')
        self.inherited=self.hard_vector(a,baseline=True)
        self.query_cache.clear();self.feas_cache.clear();return a
    def best(self,players,terminal=False,manager=True):
        return self._solve(players,terminal=terminal,manager=manager,mode='SOFT' if manager else 'FEASIBLE')
    def active_fill(self,players,manager=True):
        a=self._solve(players,manager=manager,mode='ACTIVE')
        return -1 if a is None else sum(p.slot_class=='ACTIVE' for p in a)
    def can_complete(self,owned,available,remaining_picks,manager=True):
        # A legal terminal witness cannot excuse an illegal intermediate roster.
        if self._solve(owned,manager=manager) is None:return False
        return self._solve(owned,available,remaining_picks,True,manager) is not None
    def candidate_reason(self,owned,candidate,pool,picks_after,manager=True):
        current=tuple(owned)+(candidate,)
        if self._solve(current,manager=manager) is None:
            if manager and self._solve(current,manager=False) is not None:return 'MANAGER_HARD_CONSTRAINT'
            return 'NO_LEGAL_ROSTER_ASSIGNMENT'
        if not self.can_complete(current,set(pool)-{candidate},picks_after,manager):return 'NO_FEASIBLE_COMPLETION'
        return None
    def storage_options(self,owned,terminal=True,maximum_patterns=128):
        """Exhaustive playable-subset alternatives within a deterministic pattern budget.

        Assignments within a given playable subset minimize the exact soft penalty.
        The subsequent nonlinear LINEUP search remains approximate.
        """
        key=(tuple(sorted(owned)),terminal,maximum_patterns,tuple(sorted(self.inherited.items())))
        if key in self.storage_cache:return self.storage_cache[key]
        possible=sorted(p for p in owned if any(a.slot_class not in ('ACTIVE','BENCH') for a in self.options(p)))
        capacity=sum(s['slot_class'] not in ('ACTIVE','BENCH') for s in self.slots.values())
        count=sum(math.comb(len(possible),k) for k in range(min(len(possible),capacity)+1))
        if count>maximum_patterns:raise EngineError('STORAGE_PATTERN_LIMIT',f'{count} storage patterns exceed {maximum_patterns}; no pattern silently discarded.',status='RESOURCE_LIMIT')
        result=[]
        for n in range(min(len(possible),capacity)+1):
            for subset in combinations(possible,n):
                self.stats['storage_patterns']+=1
                a=self._solve(owned,terminal=terminal,mode='SOFT',storage=set(subset))
                if a is not None:result.append(a)
        result.sort(key=self.key);self.storage_cache[key]=result;return result
    def anonymous_rosters(self,pooled_players,agents):
        """Joint pool-only allocation: legal per-agent rosters without named portfolios."""
        agents=sorted(agents,key=lambda a:a['agent_id']);ids=sorted(pooled_players)
        if len(ids)!=len(set(ids)) or sum(a['initial_roster_count'] for a in agents)!=len(ids):
            raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE','Anonymous ownership cardinalities disagree.')
        model=IntegerModel(self.maxnodes);edges=[];byplayer=defaultdict(dict);byteam=defaultdict(dict);byslot=defaultdict(dict)
        for agent in agents:
            name=agent['agent_id']
            for p in ids:
                for a in self.options(p,False):
                    j=model.var();edges.append((j,name,a));byplayer[p][j]=1.;byteam[name][j]=1.;byslot[(name,a.slot_instance_id)][j]=1.
        for p in ids:model.row(byplayer[p],low=1.,high=1.)
        for agent in agents:
            name=agent['agent_id'];model.row(byteam[name],low=agent['initial_roster_count'],high=agent['initial_roster_count'])
            for sid in self.slots:model.row(byslot[(name,sid)],high=1.)
            for rule in self.rules:
                if rule['evaluation_phase']=='AT_ALL_TIMES':
                    self._bound(model,{j:self.coefficient(a,rule) for j,who,a in edges if who==name},rule['operator'],rule['limit'])
        x=model.optimize({})
        if x is None:raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE','Pooled ownership cannot be allocated into legal anonymous states.')
        out={a['agent_id']:[] for a in agents}
        for j,name,a in edges:
            if x[j]>.5:out[name].append(a)
        if any(not self.league_valid(tuple(a),False) for a in out.values()):raise EngineError('CONSTRAINED_WITNESS_INVALID','Anonymous witness fails league rules.')
        return {k:tuple(sorted(a.player_id for a in v)) for k,v in out.items()}
