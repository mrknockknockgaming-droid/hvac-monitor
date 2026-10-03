"""Maintenance reminders (air filter, tune-up) and the system's service contractor.

An item is due after interval_days or interval_run_hours of blower time since it was last
done, whichever comes first. Blower time comes from runtime_days, which the ingest worker adds
up as snapshots arrive, so a filter in a house that runs all day in July comes due sooner.
"""
import datetime as dt

from sqlalchemy import func, select

from .db import MaintenanceItem, RuntimeDay, ServiceInfo

KINDS = {
    "filter": {"label": "Air filter", "interval_days": 90, "interval_run_hours": 500.0},
    "tuneup": {"label": "Tune-up", "interval_days": 182, "interval_run_hours": None},
}
SOON = 0.85                 # "Due soon" from 85 % of the interval
RECENT_DAYS = 14            # recent blower use, to estimate when the run-hour limit is reached


def blower_hours(s, system_id, since, until=None):
    q = select(func.coalesce(func.sum(RuntimeDay.blower_s), 0.0)).where(
        RuntimeDay.system_id == system_id, RuntimeDay.day >= since)
    if until is not None:
        q = q.where(RuntimeDay.day <= until)
    return float(s.scalar(q)) / 3600


def item_view(s, system_id, kind, row, today):
    cfg = KINDS[kind]
    days_int = row.interval_days if row else cfg["interval_days"]
    hours_int = row.interval_run_hours if row else cfg["interval_run_hours"]
    last = row.last_done if row else None
    out = {"kind": kind, "label": cfg["label"], "interval_days": days_int, "interval_run_hours": hours_int,
           "last_done": last.isoformat() if last else None, "days_since": None, "run_hours_since": None,
           "progress": None, "status": "unset", "next_due": None}
    if last is None:
        return out
    days = (today - last).days
    hours = blower_hours(s, system_id, last)
    parts, due_dates = [], []
    if days_int:
        parts.append(days / days_int)
        due_dates.append(last + dt.timedelta(days=days_int))
    if hours_int:
        parts.append(hours / hours_int)
        recent = blower_hours(s, system_id, today - dt.timedelta(days=RECENT_DAYS - 1), today) / RECENT_DAYS
        if hours >= hours_int:
            due_dates.append(today)
        elif recent > 0:
            due_dates.append(today + dt.timedelta(days=int((hours_int - hours) / recent)))
    progress = max(parts) if parts else None
    out.update(days_since=days, run_hours_since=round(hours, 1),
               progress=round(progress, 3) if progress is not None else None,
               status="unset" if progress is None else "due" if progress >= 1 else "soon" if progress >= SOON else "ok",
               next_due=min(due_dates).isoformat() if due_dates else None)
    return out


def service_view(s, system_id, today=None):
    today = today or dt.date.today()
    info = s.get(ServiceInfo, system_id)
    rows = {r.kind: r for r in s.scalars(select(MaintenanceItem).where(MaintenanceItem.system_id == system_id))}
    contractor = None
    if info is not None and (info.name or info.phone or info.email):
        contractor = {"name": info.name, "phone": info.phone, "email": info.email}
    return {"contractor": contractor, "items": [item_view(s, system_id, k, rows.get(k), today) for k in KINDS]}


def get_item(s, system_id, kind):
    """The stored row for `kind`, created with the defaults on first change."""
    row = s.scalar(select(MaintenanceItem).where(MaintenanceItem.system_id == system_id, MaintenanceItem.kind == kind))
    if row is None:
        cfg = KINDS[kind]
        row = MaintenanceItem(system_id=system_id, kind=kind, interval_days=cfg["interval_days"],
                              interval_run_hours=cfg["interval_run_hours"])
        s.add(row)
    return row
