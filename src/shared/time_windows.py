"""Calendar windows with an exclusive information boundary."""

from calendar import monthrange
from datetime import date, timedelta


def shift_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def monthly_windows(end_exclusive: date, count: int = 12) -> list[dict[str, str]]:
    boundaries = [shift_months(end_exclusive, -offset) for offset in range(count, -1, -1)]
    return [{"period": f"{start.isoformat()}/{(end - timedelta(days=1)).isoformat()}",
             "period_start": start.isoformat(), "period_end": (end - timedelta(days=1)).isoformat()}
            for start, end in zip(boundaries, boundaries[1:])]
