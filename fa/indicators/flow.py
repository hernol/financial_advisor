"""Volume indicators."""
from __future__ import annotations

from collections.abc import Sequence


def average_volume(volumes: Sequence[float | None], period: int = 20) -> float | None:
    """Mean volume over the last ``period`` sessions that reported one."""
    window = [v for v in volumes[-period:] if v is not None]
    if not window:
        return None
    return sum(window) / len(window)


def volume_ratio(volumes: Sequence[float | None], period: int = 20) -> float | None:
    """Latest volume divided by its recent average.

    The last session is excluded from the baseline so a spike is not diluted by
    the very bar being measured.
    """
    if not volumes or volumes[-1] is None:
        return None
    baseline = average_volume(volumes[:-1], period)
    if not baseline:
        return None
    return volumes[-1] / baseline
