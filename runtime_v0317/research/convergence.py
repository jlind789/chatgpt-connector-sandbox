from __future__ import annotations
import math, statistics


def mean(xs):
    return math.fsum(xs) / len(xs)


def paired_rows(runner, player_ids, n):
    """Evaluate candidates on the first n deterministic continuation indices.

    The runner's path cache guarantees prefix reuse as budgets grow. Statistics are
    paired to the current leader on identical continuation indices. Units for gaps
    are utility percentage points (100 * utility difference).
    """
    values = {}
    for pid in player_ids:
        key = (pid, runner.cfg['continuation_policy'])
        arr = runner.path_cache.setdefault(key, [])
        while len(arr) < n:
            arr.append(runner.path(pid, len(arr), runner.cfg['continuation_policy']))
        values[pid] = [x['decision_utility'] for x in arr[:n]]
    means = {pid: mean(v) for pid, v in values.items()}
    leader = max(player_ids, key=lambda p: (means[p], runner.intrinsic.value(p), p))
    rows=[]
    lv=values[leader]
    for pid in player_ids:
        diffs=[100*(a-b) for a,b in zip(values[pid],lv)]
        md=mean(diffs)
        if n > 1 and len({round(x,14) for x in diffs}) > 1:
            se=statistics.stdev(diffs)/math.sqrt(n)
        else:
            se=0.0
        rows.append({
            'player_id':pid,
            'mean_utility':means[pid],
            'mean_diff_vs_leader_points':md,
            'paired_se_points':se,
            'ci95_low_points':md-1.96*se,
            'ci95_high_points':md+1.96*se,
            'intrinsic_value':runner.intrinsic.value(pid),
        })
    rows.sort(key=lambda x:(-x['mean_utility'],-x['intrinsic_value'],x['player_id']))
    return leader, rows


def race_survivors(rows, epsilon_points=0.10, min_keep=3, max_keep=10, force=()):
    """Keep plausible challengers using paired uncertainty + deterministic cap.

    A challenger is eligible to survive when its 95% upper bound is not more than
    epsilon below the current leader. We also keep at least min_keep and explicit
    audit IDs. max_keep is a compute guard, not a statistical claim.
    """
    keep={x['player_id'] for x in rows[:min_keep]} | set(force)
    for row in rows:
        if row['ci95_high_points'] >= -epsilon_points:
            keep.add(row['player_id'])
    if len(keep) > max_keep:
        ordered=[x['player_id'] for x in rows if x['player_id'] in keep]
        forced=[p for p in force if p in keep]
        trimmed=[]
        for p in ordered:
            if p not in trimmed:
                trimmed.append(p)
            if len(trimmed)>=max_keep:break
        for p in forced:
            if p not in trimmed:
                if len(trimmed)>=max_keep:trimmed[-1]=p
                else:trimmed.append(p)
        keep=set(trimmed)
    return sorted(keep)


def resolution(rows, epsilon_points=0.10):
    """Return whether leader separation is both directional and practically material.

    Every surviving challenger must have a 95% upper bound below zero (directional
    separation), while its mean deficit must exceed epsilon (practical separation).
    """
    if len(rows) <= 1:
        return True, None
    worst=max(x['ci95_high_points'] for x in rows[1:])
    smallest_gap=min(-x['mean_diff_vs_leader_points'] for x in rows[1:])
    return worst < 0.0 and smallest_gap > epsilon_points, worst
