"""Identity-free, exact integer legality queries for one immutable snapshot.

Only FEASIBLE and ACTIVE cardinality queries are compressed.  Forecasts, player
quality, soft-policy optimization, storage alternatives, anonymous roster
allocation and nonlinear lineup optimization stay on the v0.3.6 code paths.

Two players are interchangeable *for legality* iff their full permitted
(slot, assigned-position, rule-coefficient) edge sets agree. Slot instances are
merged only if their requirements/classes and incident typed edges agree.
Integer counts therefore have a one-to-one expandable relationship with legal
individual assignments. Every returned witness is expanded to actual unique
player IDs and independently validated. A cache hit is not an unverified guess.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from decision_engine.assignment import Placement
from decision_engine.validation import EngineError
from research.constrained_assignment import ConstrainedRosterSolver, IntegerModel


@dataclass(frozen=True)
class TypedLayout:
    player_type: dict[str, int]
    signatures: tuple
    slot_groups: tuple[tuple[str, ...], ...]
    # (type, group, assigned position, full counting-coefficient tuple)
    edges: tuple


class EfficientRosterSolver(ConstrainedRosterSolver):
    """Drop-in certified query acceleration; no persistent/cross-snapshot cache."""

    def __init__(self, data: dict[str, Any]):
        super().__init__(data)
        self.layouts: dict[bool, TypedLayout] = {}
        self.typed_cache: dict[tuple, tuple | None] = {}
        self.stats.update(typed_queries=0, typed_cache_hits=0, typed_milp_solves=0,
                          expanded_witnesses=0, max_typed_variables=0,
                          max_types=0, max_slot_groups=0)

    def layout(self, manager: bool) -> TypedLayout:
        if manager in self.layouts:
            return self.layouts[manager]
        hard_policies = [p for p in self.policies if any(p.get(b,{}).get('enforcement')=='HARD' for b in ('minimum','maximum'))]
        counted = self.rules + (hard_policies if manager else [])
        signatures = {}
        for pid in sorted(self.lp):
            signatures[pid] = tuple(
                (a.slot_instance_id, a.assigned_position_id,
                 tuple(self.coefficient(a, r) for r in counted))
                for a in self.options(pid, locked=manager))
        types = tuple(sorted(set(signatures.values())))
        indexes = {s: i for i, s in enumerate(types)}
        player_type = {p: indexes[s] for p, s in signatures.items()}
        incident: dict[str, list] = defaultdict(list)
        for t, sig in enumerate(types):
            for sid, position, coeff in sig:
                incident[sid].append((t, position, coeff))
        groups = defaultdict(list)
        for sid, slot in sorted(self.slots.items()):
            key = (slot['slot_class'], slot['counts_toward_roster_total'],
                   slot['required_at_completion'], tuple(incident[sid]))
            groups[key].append(sid)
        slot_groups = tuple(sorted(tuple(v) for v in groups.values()))
        edges = []
        for g, sids in enumerate(slot_groups):
            for t, position, coeff in incident[sids[0]]:
                edges.append((t, g, position, coeff))
        result = TypedLayout(player_type, types, slot_groups, tuple(sorted(edges)))
        self.layouts[manager] = result
        self.stats['max_types'] = max(self.stats['max_types'], len(types))
        self.stats['max_slot_groups'] = max(self.stats['max_slot_groups'], len(slot_groups))
        return result

    def _solve(self, owned, pool=(), remaining=0, terminal=False, manager=True,
               mode='FEASIBLE', storage=None):
        if mode not in ('FEASIBLE', 'ACTIVE') or storage is not None:
            return super()._solve(owned, pool, remaining, terminal, manager, mode, storage)
        owned = tuple(sorted(owned))
        pool = tuple(sorted(set(pool) - set(owned)))
        if (len(set(owned)) != len(owned) or remaining < 0 or remaining > len(pool)
                or len(owned) + remaining > len(self.slots)):
            return None
        layout = self.layout(manager)
        own_ids = defaultdict(list)
        pool_ids = defaultdict(list)
        for pid in owned:
            own_ids[layout.player_type[pid]].append(pid)
        if remaining:
            for pid in pool:
                pool_ids[layout.player_type[pid]].append(pid)
        counts = tuple((t, len(own_ids[t]), min(len(pool_ids[t]), remaining))
                       for t in sorted(set(own_ids) | set(pool_ids)))
        # No legal continuation can consume > remaining players of any one type.
        # Capping excess supply is exact, but never caps below actual shortages.
        key = (manager, terminal, mode, remaining, counts,
               tuple(sorted(self.inherited.items())))
        self.stats['typed_queries'] += 1
        if key in self.typed_cache:
            self.stats['typed_cache_hits'] += 1
            witness = self.typed_cache[key]
        else:
            witness = self._typed_solve(layout, counts, remaining, terminal, manager, mode)
            self.typed_cache[key] = witness
        if witness is None:
            return None
        assignment = self._expand(layout, witness, own_ids, pool_ids, manager)
        if (len(assignment) != len(owned) + remaining
                or not set(owned) <= {a.player_id for a in assignment}
                or not self.league_valid(assignment, terminal)
                or (manager and not self.manager_valid(assignment, terminal))):
            raise EngineError('TYPED_WITNESS_INVALID', 'Expanded assignment failed independent rule checks.')
        self.stats['expanded_witnesses'] += 1
        return assignment

    def _typed_solve(self, layout, counts, remaining, terminal, manager, mode):
        model = IntegerModel(self.maxnodes)
        supplied = {t: (n, available) for t, n, available in counts}
        bytype = defaultdict(dict)
        byslot = defaultdict(dict)
        columns = []
        for t, g, position, coeff in layout.edges:
            if t not in supplied or sum(supplied[t]) == 0:
                continue
            capacity = len(layout.slot_groups[g])
            j = model.var(high=min(capacity, sum(supplied[t])), integer=True)
            columns.append((j, t, g, position, coeff))
            bytype[t][j] = 1.
            byslot[g][j] = 1.
        for t, n, available in counts:
            model.row(bytype[t], low=n, high=n + available)
        for g, sids in enumerate(layout.slot_groups):
            required = terminal and self.slots[sids[0]]['required_at_completion']
            model.row(byslot[g], low=len(sids) if required else 0, high=len(sids))
        cardinality = sum(n for _, n, _ in counts) + remaining
        model.row({j: 1. for j, *_ in columns}, low=cardinality, high=cardinality)
        for k, rule in enumerate(self.rules):
            if terminal or rule['evaluation_phase'] == 'AT_ALL_TIMES':
                self._bound(model, {j: c[k] for j, t, g, p, c in columns},
                            rule['operator'], rule['limit'])
        if manager:
            hard_policies = [p for p in self.policies if any(p.get(b,{}).get('enforcement')=='HARD' for b in ('minimum','maximum'))]
            for k, policy in enumerate(hard_policies, len(self.rules)):
                coefficients = {j: c[k] for j, t, g, p, c in columns}
                for name, operator in [('minimum', 'GTE'), ('maximum', 'LTE')]:
                    bound = policy.get(name)
                    if not bound or bound['enforcement'] != 'HARD':
                        continue
                    if not terminal and bound['evaluation_phase'] != 'AT_ALL_TIMES':
                        continue
                    inherited = self.inherited.get(policy['policy_id'] + '/' + name, 0)
                    adjusted = bound['value'] + inherited if operator == 'LTE' else bound['value'] - inherited
                    self._bound(model, coefficients, operator, adjusted)
        objective = {j: -float(self.slots[layout.slot_groups[g][0]]['slot_class'] == 'ACTIVE')
                     for j, t, g, p, c in columns} if mode == 'ACTIVE' else {}
        self.stats['max_typed_variables'] = max(self.stats['max_typed_variables'], len(model.lb))
        self.stats['typed_milp_solves'] += 1
        x = model.optimize(objective)
        if x is None:
            return None
        return tuple((t, g, position, int(x[j])) for j, t, g, position, _ in columns if x[j] > 0.5)

    def _expand(self, layout, witness, own_ids, pool_ids, manager):
        needed = Counter()
        for t, g, position, count in witness:
            needed[t] += count
        ids = {}
        for t, count in needed.items():
            owned = own_ids.get(t, [])
            ids[t] = list(owned) + list(pool_ids.get(t, []))[:count - len(owned)]
            if count < len(owned) or len(ids[t]) != count:
                raise EngineError('TYPED_WITNESS_INVALID', 'Type certificate exceeds real player supply.')
        if any(own_ids[t] and t not in needed for t in own_ids):
            raise EngineError('TYPED_WITNESS_INVALID', 'Type certificate omitted mandatory players.')
        used_type = Counter()
        used_slot = Counter()
        result = []
        for t, g, position, count in witness:
            for _ in range(count):
                pid = ids[t][used_type[t]]
                sid = layout.slot_groups[g][used_slot[g]]
                slot = self.slots[sid]
                a = Placement(pid, sid, position, slot['slot_class'], slot['counts_toward_roster_total'])
                if a not in self.options(pid, manager):
                    raise EngineError('TYPED_WITNESS_INVALID', 'Type certificate violates eligibility/status/lock.')
                used_type[t] += 1
                used_slot[g] += 1
                result.append(a)
        if len({a.player_id for a in result}) != len(result) or len({a.slot_instance_id for a in result}) != len(result):
            raise EngineError('TYPED_WITNESS_INVALID', 'Expanded assignment repeats a player or slot.')
        return tuple(sorted(result, key=lambda a: a.player_id))
