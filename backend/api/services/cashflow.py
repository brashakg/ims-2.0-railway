"""
IMS 2.0 - Cash-flow forecast engine
===================================
Pure, DB-free math that turns a list of expected money-IN and money-OUT
"events" into a week-by-week cash-flow projection with a running balance and a
lowest-point (cash-crunch) warning.

The finance router assembles the events from real data:
  inflows  = unpaid customer orders (AR), projected to a collection date
  outflows = outstanding vendor bills (AP) on their due date, plus recurring
             payroll / expense estimates

and calls build_forecast(). Keeping the math here makes it unit-testable and
keeps the endpoint thin.

Event shape: {"date": ISO-date-or-datetime, "amount": float, "label": str?}.
An event whose date is in the past (before as_of) is treated as due in week 0
(it should already have happened, so it weighs on the immediate position).
Events beyond the horizon are excluded from the weeks but summed separately.
"""

from datetime import datetime, timedelta
from typing import List, Optional


def _parse(s) -> Optional[datetime]:
    if isinstance(s, datetime):
        return s
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    if not txt:
        return None
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(txt[:10])
    except ValueError:
        return None


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def week_buckets(as_of: datetime, days: int) -> List[dict]:
    """A list of consecutive 7-day buckets covering [as_of, as_of + days)."""
    num_weeks = max(1, (int(days) + 6) // 7)
    out = []
    for i in range(num_weeks):
        start = as_of + timedelta(days=7 * i)
        end = start + timedelta(days=7)
        out.append(
            {
                "index": i,
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "label": f"Week {i + 1}",
            }
        )
    return out


def _bucket_index(date: datetime, as_of: datetime, num_weeks: int) -> Optional[int]:
    """Which week bucket a date falls in. Past dates -> 0. Beyond horizon -> None."""
    delta_days = (date.date() - as_of.date()).days
    if delta_days < 0:
        return 0
    idx = delta_days // 7
    if idx >= num_weeks:
        return None
    return idx


def build_forecast(
    opening_cash,
    inflow_events: List[dict],
    outflow_events: List[dict],
    as_of_iso: Optional[str] = None,
    days: int = 90,
) -> dict:
    """Project a running cash balance over `days`, in weekly buckets.

    Returns:
      {
        opening_cash, as_of, horizon_days,
        weeks: [{index,start,end,label,inflow,outflow,net,closing_balance}],
        totals: {inflow, outflow, net, closing_balance},
        beyond_horizon: {inflow, outflow},
        lowest: {week_index, week_start, balance}   # cash-crunch low point
      }
    """
    as_of = _parse(as_of_iso) or datetime.utcnow()
    buckets = week_buckets(as_of, days)
    num_weeks = len(buckets)

    inflow_by_week = [0.0] * num_weeks
    outflow_by_week = [0.0] * num_weeks
    beyond_in = 0.0
    beyond_out = 0.0

    for ev in inflow_events or []:
        d = _parse(ev.get("date")) if isinstance(ev, dict) else None
        amt = _f(ev.get("amount")) if isinstance(ev, dict) else 0.0
        if amt <= 0:
            continue
        if d is None:
            inflow_by_week[0] = round(inflow_by_week[0] + amt, 2)
            continue
        idx = _bucket_index(d, as_of, num_weeks)
        if idx is None:
            beyond_in = round(beyond_in + amt, 2)
        else:
            inflow_by_week[idx] = round(inflow_by_week[idx] + amt, 2)

    for ev in outflow_events or []:
        d = _parse(ev.get("date")) if isinstance(ev, dict) else None
        amt = _f(ev.get("amount")) if isinstance(ev, dict) else 0.0
        if amt <= 0:
            continue
        if d is None:
            outflow_by_week[0] = round(outflow_by_week[0] + amt, 2)
            continue
        idx = _bucket_index(d, as_of, num_weeks)
        if idx is None:
            beyond_out = round(beyond_out + amt, 2)
        else:
            outflow_by_week[idx] = round(outflow_by_week[idx] + amt, 2)

    opening = _f(opening_cash)
    running = opening
    weeks = []
    lowest_balance = opening
    lowest_index = -1  # -1 means the opening position is the low point
    lowest_start = as_of.date().isoformat()

    for b in buckets:
        i = b["index"]
        inflow = inflow_by_week[i]
        outflow = outflow_by_week[i]
        net = round(inflow - outflow, 2)
        running = round(running + net, 2)
        weeks.append(
            {
                "index": i,
                "start": b["start"],
                "end": b["end"],
                "label": b["label"],
                "inflow": inflow,
                "outflow": outflow,
                "net": net,
                "closing_balance": running,
            }
        )
        if running < lowest_balance:
            lowest_balance = running
            lowest_index = i
            lowest_start = b["start"]

    total_in = round(sum(inflow_by_week), 2)
    total_out = round(sum(outflow_by_week), 2)

    return {
        "opening_cash": opening,
        "as_of": as_of.date().isoformat(),
        "horizon_days": int(days),
        "weeks": weeks,
        "totals": {
            "inflow": total_in,
            "outflow": total_out,
            "net": round(total_in - total_out, 2),
            "closing_balance": running,
        },
        "beyond_horizon": {"inflow": beyond_in, "outflow": beyond_out},
        "lowest": {
            "week_index": lowest_index,
            "week_start": lowest_start,
            "balance": lowest_balance,
        },
    }
