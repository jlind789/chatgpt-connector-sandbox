"""Exact category-CDF acceleration and an explicitly fixed management ablation."""
from __future__ import annotations
from bisect import bisect_left, bisect_right
import math
from decision_engine.scoring import category_value, rounded
from .drafts import MemoEvaluator
from .independent import lineup_plan, validate_audit_scope

class CDFEvaluator(MemoEvaluator):
    """Same category objective as MemoEvaluator; sort reference thresholds once.

    Rich participation semantics retain the original scalar evaluator. No score
    approximation, player shortlisting, or domain-rule relaxation is introduced.
    """
    optimizer_status = 'EXACT_ENUMERATION_STATIC_INFORMATION'
    def __init__(self, *args):
        super().__init__(*args)
        self.cdf={}
        for c in self.categories:
            cells={};undefined=[]
            for f in self.field:
                x=category_value(c,f['stat_values'])
                if x is None:undefined.append(f['weight'])
                else:cells.setdefault(rounded(x,c['precision']),[]).append(f['weight'])
            keys=sorted(cells)
            weights=[math.fsum(cells[x]) for x in keys]
            prefixes=[math.fsum(weights[:i]) for i in range(len(keys)+1)]
            self.cdf[c['category_id']] = (keys,prefixes,math.fsum(undefined))

    def score_stats(self,stats):
        if self.rules['minimum_participation']:
            return super().score_stats(stats)
        key=tuple(stats.get(k,0.) for k in self.stats)
        if key not in self.stats_cache:
            credits={}
            for c in self.categories:
                x=category_value(c,stats)
                k=None if x is None else rounded(x,c['precision'])
                ck=(c['category_id'],k)
                if ck not in self.credit_cache:
                    keys,prefix,undefined=self.cdf[c['category_id']]
                    if k is None:
                        credit=undefined*self.objective['both_undefined_tie_credit']/self.field_weight
                    else:
                        left=bisect_left(keys,k);right=bisect_right(keys,k)
                        tied=prefix[right]-prefix[left]
                        won=prefix[left] if c['direction']=='HIGHER_IS_BETTER' else prefix[-1]-prefix[right]
                        credit=(won+tied*self.objective['category_tie_credit']+undefined)/self.field_weight
                    self.credit_cache[ck]=credit
                credits[c['category_id']]=self.credit_cache[ck]
            league=math.fsum(c['weight']*credits[c['category_id']] for c in self.categories)/math.fsum(c['weight'] for c in self.categories)
            strategy=math.fsum(self.weights[cid]*credits[cid] for cid in sorted(credits))/math.fsum(self.weights.values())
            self.stats_cache[key]=(league,strategy,credits)
        return self.stats_cache[key]

class AdditiveManagementEvaluator(CDFEvaluator):
    """Counterfactual: value the roster under the audit's additive forecast manager.

    This is not an optimized lineup-utility claim. Hard construction constraints
    still come from the shared exact solver. Unsupported audit scope fails visibly.
    """
    optimizer_status='FIXED_ADDITIVE_PUBLIC_FORECAST_LINEUP'
    def __init__(self,*args):
        super().__init__(*args)
        validate_audit_scope(self.data)
        if self.data['manager_profile']['category_preferences']:
            raise ValueError('Aligned management ablation currently uses STANDARD categories only.')

    def evaluate(self,owned,terminal=True):
        key=(tuple(sorted(owned)),terminal)
        if key in self.cache:return self.cache[key]
        constructions=self.solver.all(owned,terminal,True)
        if not constructions:return None
        rows=[]
        for a in constructions:
            penalty,effects=self.solver.soft_penalty(a)
            rows.append((penalty,self.solver.key(a),a,effects))
        penalty,_,construction,effects=min(rows,key=lambda r:(r[0],r[1]))
        wire=lineup_plan(self.data,sorted(owned))
        plan={}
        for group,assignments in wire.items():
            plan[group]=tuple(min(
                (a for a in self.solver.options(row['player_id'],locked=False)
                 if a.slot_instance_id==row['slot_instance_id']),
                key=lambda a:a.assigned_position_id) for row in assignments)
        evaluated=self.evaluate_fixed_plan(plan)
        self.evaluated_plans+=1
        result={**evaluated,'decision_utility':evaluated['strategy_utility']-penalty,
            'soft_policy_penalty':penalty,'policy_effects':effects,
            'construction_assignment':[a.output() for a in construction],
            'lineup_plan':{g:[a.output() for a in plan[g]] for g in self.groups},
            'optimizer_status':self.optimizer_status,'forecast_information_policy':'STATIC_PRE_DRAFT',
            '_plan':plan}
        self.cache[key]=result
        return result
