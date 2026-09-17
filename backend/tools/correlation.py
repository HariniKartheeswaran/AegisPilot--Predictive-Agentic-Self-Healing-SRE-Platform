"""Correlation tools — tie recent changes to the incident window.

`correlate_changes` scores each recent deploy by temporal proximity to the
incident: a change shipped shortly *before* detection is a strong suspect; one
after detection or long past is not. This is a real scoring function the agent
uses to justify its confidence number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from backend.models import Deploy
from backend.services.storage import StorageService

MIN = 60_000


def deploys_in_window(
    storage: StorageService, service: str, detected_at: int, lookback_min: int = 180
) -> list[Deploy]:
    """The affected service's own deploys within `lookback_min` before detection.

    We score the service's OWN change history: attributing an incident to the
    affected service's most recent deploy is both correct and avoids blaming an
    unrelated service that merely happened to ship around the same time.
    """
    window_start = detected_at - lookback_min * MIN
    candidates = storage.deploys_for_service(service)
    return [d for d in candidates if window_start <= d.deployed_at <= detected_at + 5 * MIN]


@dataclass
class ChangeSuspect:
    deploy: Deploy
    minutes_before: float
    proximity_score: float  # 0..1, higher = more suspicious


def _proximity(minutes_before: float) -> float:
    """Exponential decay: a deploy 0 min before detection ~1.0; ~30 min ~0.5;
    beyond a couple hours it fades toward 0. Deploys *after* detection score 0."""
    if minutes_before < 0:
        return 0.0
    half_life = 30.0
    return math.exp(-math.log(2) * minutes_before / half_life)


def correlate_changes(deploys: list[Deploy], detected_at: int) -> list[ChangeSuspect]:
    suspects: list[ChangeSuspect] = []
    for d in deploys:
        minutes_before = (detected_at - d.deployed_at) / MIN
        suspects.append(
            ChangeSuspect(
                deploy=d, minutes_before=round(minutes_before, 1),
                proximity_score=round(_proximity(minutes_before), 3),
            )
        )
    suspects.sort(key=lambda s: s.proximity_score, reverse=True)
    return suspects
