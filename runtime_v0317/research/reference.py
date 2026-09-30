"""Versioned, projection-only baseline mixture. Never uses audit outcomes."""
from __future__ import annotations
import copy
from decision_engine.validation import canonical_hash,validate
from .drafts import pure_baseline_draft
from .independent import lineup_plan,hidden_tape,sum_plan
from .worlds import keyed_seed


def generate_reference_field(world,draws_per_roster=3):
    if draws_per_roster<1:raise ValueError('At least one public forecast draw is required.')
    field=[];trace=[];data=world['snapshot']
    for policy in ('B1','B2','B3'):
        draft=pure_baseline_draft(world,policy)
        if draft['status']!='COMPLETE':raise ValueError('Reference baseline failed: '+policy)
        for team,roster in sorted(draft['rosters'].items()):
            plan=lineup_plan(data,roster)
            for draw in range(draws_per_roster):
                tape=hidden_tape(data,'FORECAST',keyed_seed('REFERENCE-v1',world['family'],world['world_seed'],len(world['team_ids'])),draw,namespace='REFERENCE_FORECAST-v1')
                stats=sum_plan(data,plan,tape)
                field.append({'reference_id':f'{policy}_{team}_{draw}','weight':1.,'stat_values':stats})
            trace.append({'baseline':policy,'team_id':team,'roster':roster,'forecast_only_lineup_plan':plan})
    result=copy.deepcopy(world);ref=result['snapshot']['reference_model']
    ref['generation_policy']='BASELINE_MIXTURE_V1';ref['field']=field
    ref['field_id']='baseline_mixture_'+canonical_hash(field)[:16]
    ref['reference_model_id']=ref['field_id']
    provenance={'version':'BASELINE_MIXTURE_V1','mixture':{'B1':1/3,'B2':1/3,'B3':1/3},'draws_per_roster':draws_per_roster,'training_namespace':'REFERENCE_FORECAST-v1','source':'PUBLIC_PRE_DRAFT_PROJECTIONS_ONLY','audit_outcomes_used':False,'sample_rows':len(field),'lineup_policy':'ADDITIVE_PUBLIC_FORECAST_MATCHING_V1','baseline_completed_rosters':trace,'field_sha256':canonical_hash(field),'generated_from_snapshot_sha256':canonical_hash(data)}
    result['reference_generation']=provenance
    validate(result['snapshot'])
    return result
