"""Forecast-only, mean-preserving hockey primitive scenarios (research candidate).

This module has no access to outcome tapes, audit families, or realized performance.
It preserves public period means AND the separate intrinsic projection snapshot.
After finite-ensemble moment matching the outcomes can be fractional: these are
production scenarios, NOT calibrated integer game simulations.
"""
from __future__ import annotations
import copy
from dataclasses import asdict, dataclass
import math
import numpy as np
from decision_engine.validation import canonical_hash, validate
from .worlds import keyed_seed

MODEL_ID = 'M033_MEAN_PRESERVING_PRIMITIVES_V1'
SUPPORTED = {'games', 'goals', 'shots', 'assists', 'saves', 'shots_against'}

@dataclass(frozen=True)
class ScenarioConfig:
    samples: int = 16
    player_log_sd: float = 0.30
    period_log_sd: float = 0.10
    seed: int = 7330301
    namespace: str = 'M033_PUBLIC_UNCERTAINTY_V1'

    def validate(self) -> None:
        if type(self.samples) is not int or not 2 <= self.samples <= 512:
            raise ValueError('Scenario samples must be an integer in [2,512].')
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError('Scenario seed must be a nonnegative integer.')
        for x in (self.player_log_sd, self.period_log_sd):
            if not math.isfinite(x) or not 0 <= x <= 1:
                raise ValueError('Research log standard deviations must be finite in [0,1].')
        if not self.namespace or 'AUDIT' in self.namespace.upper():
            raise ValueError('Use an explicit forecast-only random-stream namespace.')


def public_period_means(data: dict) -> dict:
    scenarios = data['exposure_model']['forecast_scenarios']
    stats = sorted(x['stat_key'] for x in data['league']['stat_definitions'])
    return {p['player_id']: {t['period_id']: {
        k: math.fsum(s['probability'] * s['player_period_stats'][p['player_id']][t['period_id']].get(k, 0.) for s in scenarios)
        for k in stats} for t in sorted(data['exposure_model']['periods'], key=lambda x:x['period_id'])}
        for p in sorted(data['players'],key=lambda x:x['player_id'])}


def match_mean(raw: np.ndarray, target: float) -> tuple[np.ndarray, bool]:
    """Rescale a nonnegative primitive; never independently rescale its total."""
    raw = np.asarray(raw, dtype=float)
    if raw.ndim != 1 or len(raw) < 2 or not np.isfinite(raw).all() or np.any(raw < 0):
        raise ValueError('Expected a finite nonnegative one-dimensional ensemble.')
    if not math.isfinite(target) or target < 0:
        raise ValueError('Expected a nonnegative finite target.')
    if target == 0:
        return np.zeros_like(raw), False
    total = math.fsum(float(v) for v in raw)
    if total == 0:
        # Rare event absent from this ensemble: no fabricated event support.
        return np.full_like(raw, target), True
    return raw * (target * len(raw) / total), False


def with_uncertainty(data: dict, config: ScenarioConfig = ScenarioConfig()) -> tuple[dict,dict]:
    config.validate()
    d = validate(data)
    keys = {s['stat_key'] for s in d['league']['stat_definitions']}
    if not keys <= SUPPORTED or 'games' not in keys:
        raise ValueError('This research scenario adapter supports the declared hockey primitive keys only.')
    if ('goals' in keys) != ('shots' in keys) or ('saves' in keys) != ('shots_against' in keys):
        raise ValueError('Goals/shots and saves/shots-against must be supplied in pairs.')
    if len(d['exposure_model']['forecast_scenarios']) != 1:
        raise ValueError('Do not silently replace an already uncertain forecast with a new ensemble.')
    means = public_period_means(d)
    base = keyed_seed(config.namespace, config.seed)
    n = config.samples
    periods = sorted(next(iter(means.values())))
    pace = {t: np.random.default_rng(keyed_seed(base, 'period', t)).lognormal(
        -.5*config.period_log_sd**2, config.period_log_sd, n) for t in periods}
    scenarios = [{'scenario_id':f'm033_{i:04d}', 'probability':1/n,
        'player_period_stats':{p:{} for p in sorted(means)}} for i in range(n)]
    fallback = []
    max_error = 0.
    for pid in sorted(means):
        talent = np.random.default_rng(keyed_seed(base, 'player', pid)).lognormal(
            -.5*config.player_log_sd**2, config.player_log_sd, n)
        for period in periods:
            v = means[pid][period]
            if v['games'] not in (0.,1.):
                raise ValueError('Scenario adapter currently requires explicit zero/one projected appearance per period; no guessed availability discount.')
            if not v['games'] and any(x for k,x in v.items() if k != 'games'):
                raise ValueError('Production without a projected appearance is unsupported.')
            if v.get('goals',0)>v.get('shots',0) or v.get('saves',0)>v.get('shots_against',0):
                raise ValueError('Invalid primitive means.')
            primitive_means = {}
            if 'goals' in keys:
                primitive_means.update(goals=v['goals'], non_goal_shots=v['shots']-v['goals'])
            if 'assists' in keys: primitive_means['assists']=v['assists']
            if 'saves' in keys:
                primitive_means.update(saves=v['saves'], goals_against=v['shots_against']-v['saves'])
            primitive = {}
            for name, target in sorted(primitive_means.items()):
                rng = np.random.default_rng(keyed_seed(base,pid,period,name))
                raw = rng.poisson(target * talent * pace[period])
                values, used_fallback = match_mean(raw, target)
                primitive[name]=values
                if used_fallback: fallback.append({'player_id':pid,'period_id':period,'primitive':name,'mean':target})
            for i in range(n):
                values = {k:0. for k in sorted(keys)}
                values['games']=v['games']
                if 'goals' in keys:
                    values['goals']=float(primitive['goals'][i])
                    values['shots']=values['goals']+float(primitive['non_goal_shots'][i])
                if 'assists' in keys: values['assists']=float(primitive['assists'][i])
                if 'saves' in keys:
                    values['saves']=float(primitive['saves'][i])
                    values['shots_against']=values['saves']+float(primitive['goals_against'][i])
                scenarios[i]['player_period_stats'][pid][period]=values
            for k in keys:
                after=math.fsum(s['probability']*s['player_period_stats'][pid][period][k] for s in scenarios)
                max_error=max(max_error,abs(after-v[k]))
    before=canonical_hash(d['projection_set'])
    d['exposure_model']['forecast_scenarios']=scenarios
    d['exposure_model']['exposure_model_id']='m033_uncertainty_'+canonical_hash(scenarios)[:20]
    validate(d)
    if canonical_hash(d['projection_set']) != before:
        raise AssertionError('Uncertainty changed intrinsic projection snapshot.')
    provenance={'model_id':MODEL_ID,'config':asdict(config),'source':'PUBLIC_PROJECTIONS_ONLY',
        'projection_set_sha256':before,'scenario_sha256':canonical_hash(scenarios),
        'maximum_period_mean_error':max_error,'zero_sample_primitive_fallbacks':fallback,
        'playing_time_reduced':False,'audit_family_used':False,
        'distribution_status':'UNCALIBRATED_FRACTIONAL_MEAN_MATCHED_PRODUCTION_SCENARIOS',
        'limitations':['Not integer game realizations after rescaling','No additional missed-appearance model',
            'Rare-event all-zero ensembles fall back to a declared constant mean',
            'Same public prior for every player; no knowledge of hidden role changes']}
    return d, provenance
