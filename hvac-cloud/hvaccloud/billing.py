"""Billing (Fullscope Office): invoices that staff send by email and mark paid by hand. No card payments.

Prices: each kit once (when shipped to a customer) and each system monthly. The list prices are the
landing page's web/pricing.json (dollars); a customer can have their own. "Draft this month's invoices"
makes one draft per active customer with a line per kit shipped and not billed yet and a line per
system on their account. Drafts can be edited, then sent (emailed; issued today, due in INVOICE_DAYS)
or marked sent by hand (printed and posted), then marked paid. Amounts are whole cents.

Mounted by api.create_app, which hands over its database session, sign-in and email helpers.
"""
import calendar
import datetime as dt
import json
import os
import re
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import settings
from .db import Customer, Invoice, InvoiceLine, Kit, System, as_utc, utcnow

PRICING_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "pricing.json")
STATUSES = ("draft", "sent", "paid", "void")
MOVES = {"draft": ("sent", "paid", "void"), "sent": ("draft", "paid", "void"), "paid": ("sent",), "void": ("draft",)}
MAX_CENTS = 100_000_000
PERIOD_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class LineIn(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    qty: int = Field(1, ge=1, le=10000)
    unit_cents: int = Field(ge=-MAX_CENTS, le=MAX_CENTS)          # below zero: a credit
    kit_id: int | None = None
    system_id: int | None = None


class InvoiceIn(BaseModel):
    customer_id: int
    lines: list[LineIn] = Field([], max_length=200)
    notes: str | None = Field(None, max_length=2000)


class InvoicePatch(BaseModel):
    lines: list[LineIn] | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)
    due: dt.date | None = None
    status: Literal["draft", "sent", "paid", "void"] | None = None
    paid_on: dt.date | None = None
    paid_note: str | None = Field(None, max_length=200)


class RunIn(BaseModel):
    period: str = Field(max_length=7)                             # 2026-10
    customer_id: int | None = None


def _iso(t):
    return as_utc(t).isoformat() if t else None


def _clean(v):
    v = (v or "").strip()
    return v or None


def money(cents):
    """$1,234.56 (and -$5.00 for a credit)."""
    c = abs(cents)
    return f"{'-' if cents < 0 else ''}${c // 100:,}.{c % 100:02d}"


def month_name(period):
    y, m = period.split("-")
    return f"{calendar.month_name[int(m)]} {y}"


def list_prices():
    """The landing page's prices in cents: {"kit_cents": ..., "monthly_cents": ...}; None when not set."""
    try:
        with open(PRICING_FILE, encoding="utf-8") as f:
            p = json.load(f)
    except (OSError, ValueError):
        p = {}

    def cents(v):
        return round(float(v) * 100) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return {"kit_cents": cents(p.get("kit")), "monthly_cents": cents(p.get("monitoring"))}


def prices_for(c):
    """A customer's prices in cents: their own, else the list price; None when neither is set."""
    lp = list_prices()
    return (c.kit_cents if c.kit_cents is not None else lp["kit_cents"],
            c.monthly_cents if c.monthly_cents is not None else lp["monthly_cents"])


def lines_of(s, inv):
    return s.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id).order_by(InvoiceLine.id)).all()


def overdue(inv):
    return inv.status == "sent" and inv.due is not None and inv.due < dt.date.today()


def invoice_row(s, inv, customer=None):
    c = customer or s.get(Customer, inv.customer_id)
    lines = lines_of(s, inv)
    return {"id": inv.id, "number": 1000 + inv.id, "status": inv.status, "period": inv.period,
            "customer": {"id": c.id, "company": c.company}, "created_at": _iso(inv.created_at),
            "issued": inv.issued.isoformat() if inv.issued else None, "due": inv.due.isoformat() if inv.due else None,
            "paid_on": inv.paid_on.isoformat() if inv.paid_on else None, "overdue": overdue(inv),
            "total_cents": sum(x.qty * x.unit_cents for x in lines), "lines": len(lines)}


def summary(s):
    """Counts for the Office header: drafts, sent and unpaid, overdue, and what is owed."""
    out = {"draft": 0, "sent": 0, "overdue": 0, "unpaid_cents": 0}
    for inv in s.scalars(select(Invoice).where(Invoice.status.in_(("draft", "sent")))):
        out[inv.status] += 1
        if inv.status == "sent":
            out["unpaid_cents"] += sum(x.qty * x.unit_cents for x in lines_of(s, inv))
            out["overdue"] += overdue(inv)
    return out


def customer_invoices(s, c):
    rows = s.scalars(select(Invoice).where(Invoice.customer_id == c.id).order_by(Invoice.id.desc()))
    return [invoice_row(s, inv, c) for inv in rows]


def mount(app, db, principal, send_mail):

    def staff_only(who=Depends(principal)):
        if not who.staff:
            raise HTTPException(403, "for Fullscope staff only")
        return who

    def get_invoice(s, invoice_id):
        inv = s.get(Invoice, invoice_id)
        if inv is None:
            raise HTTPException(404, "no such invoice")
        return inv

    def detail(s, inv):
        c = s.get(Customer, inv.customer_id)
        lines = lines_of(s, inv)
        return {**invoice_row(s, inv, c), "notes": inv.notes, "paid_note": inv.paid_note, "sent_at": _iso(inv.sent_at),
                "emailed": inv.emailed, "pay_note": settings.INVOICE_NOTE or None, "from_email": settings.ORDER_EMAIL or None,
                "bill_to": {"company": c.company, "contact": c.contact, "email": c.email, "address": c.address},
                "lines": [{"id": x.id, "description": x.description, "qty": x.qty, "unit_cents": x.unit_cents,
                           "amount_cents": x.qty * x.unit_cents, "kit_id": x.kit_id, "system_id": x.system_id} for x in lines]}

    def set_lines(s, inv, lines):
        for x in lines:
            if not x.description.strip():
                raise HTTPException(422, "Every line needs a description.")
            if x.kit_id is not None and s.get(Kit, x.kit_id) is None:
                raise HTTPException(422, "no such kit")
            if x.system_id is not None and s.get(System, x.system_id) is None:
                raise HTTPException(422, "no such system")
        for old in lines_of(s, inv):
            s.delete(old)
        s.flush()
        for x in lines:
            s.add(InvoiceLine(invoice_id=inv.id, description=x.description.strip(), qty=x.qty, unit_cents=x.unit_cents,
                              kit_id=x.kit_id, system_id=x.system_id))

    def issue(s, inv):
        """Sent or paid: it needs a line, gets today's date and a due date."""
        if not lines_of(s, inv):
            raise HTTPException(422, "Add a line first.")
        if inv.issued is None:
            inv.issued = dt.date.today()
        if inv.due is None:
            inv.due = inv.issued + dt.timedelta(days=settings.INVOICE_DAYS)

    def email_text(s, inv):
        c = s.get(Customer, inv.customer_id)
        lines = lines_of(s, inv)
        out = [f"Invoice {1000 + inv.id} from Fullscope", f"Date: {inv.issued.isoformat()}", f"Due: {inv.due.isoformat()}", "",
               f"Bill to: {c.company}"]
        out += [v for v in (c.contact, c.address) if v]
        out.append("")
        out += [f"{x.description}: {x.qty} x {money(x.unit_cents)} = {money(x.qty * x.unit_cents)}" for x in lines]
        out += ["", f"Total due: {money(sum(x.qty * x.unit_cents for x in lines))}"]
        for v in (inv.notes, settings.INVOICE_NOTE):
            if v:
                out += ["", v]
        out += ["", "Questions about this invoice? Reply to this email."]
        return "\n".join(out)

    @app.get("/api/office/invoices")
    def invoices(status: Literal["open", "draft", "sent", "paid", "void", "all"] = "open", who=Depends(staff_only), s=Depends(db)):
        """Invoices, newest first. "open" = drafts and sent ones not paid yet."""
        q = select(Invoice)
        if status == "open":
            q = q.where(Invoice.status.in_(("draft", "sent")))
        elif status != "all":
            q = q.where(Invoice.status == status)
        rows = s.scalars(q.order_by(Invoice.id.desc()).limit(500)).all()
        customers = {c.id: c for c in s.scalars(select(Customer).where(Customer.id.in_({i.customer_id for i in rows})))}
        return [invoice_row(s, inv, customers[inv.customer_id]) for inv in rows]

    @app.post("/api/office/invoices")
    def add_invoice(body: InvoiceIn, who=Depends(staff_only), s=Depends(db)):
        """A draft for one customer, empty or with the lines given."""
        if s.get(Customer, body.customer_id) is None:
            raise HTTPException(404, "no such customer")
        inv = Invoice(customer_id=body.customer_id, notes=_clean(body.notes))
        s.add(inv)
        s.flush()
        set_lines(s, inv, body.lines)
        s.commit()
        return detail(s, inv)

    @app.post("/api/office/invoices/run")
    def run(body: RunIn, who=Depends(staff_only), s=Depends(db)):
        """This month's drafts: one per active customer (or the one given) with a line per kit shipped to them
        and not billed yet, and one per system on their account. Customers billed for the month already, or
        with nothing to bill, are skipped."""
        period = body.period.strip()
        if not PERIOD_RE.match(period):
            raise HTTPException(422, "A month looks like 2026-10.")
        if body.customer_id is not None:
            c = s.get(Customer, body.customer_id)
            if c is None:
                raise HTTPException(404, "no such customer")
            todo = [c]
        else:
            todo = s.scalars(select(Customer).where(Customer.status == "active").order_by(Customer.company, Customer.id)).all()
        billed_kits = set(s.scalars(select(InvoiceLine.kit_id).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
                                    .where(InvoiceLine.kit_id.is_not(None), Invoice.status != "void")))
        created, skipped, missing = [], [], False
        for c in todo:
            who_ = {"id": c.id, "company": c.company}
            if s.scalar(select(Invoice.id).where(Invoice.customer_id == c.id, Invoice.period == period, Invoice.status != "void")):
                skipped.append({"customer": who_, "reason": f"Already has an invoice for {month_name(period)}."})
                continue
            kit_price, monthly = prices_for(c)
            lines = []
            for k in s.scalars(select(Kit).where(Kit.customer_id == c.id, Kit.shipped_at.is_not(None)).order_by(Kit.shipped_at, Kit.id)):
                if k.id not in billed_kits:
                    lines.append(LineIn(description=f"Fullscope kit {k.code}", unit_cents=kit_price or 0, kit_id=k.id))
                    missing = missing or kit_price is None
            if c.account_id:
                for system in s.scalars(select(System).where(System.account_id == c.account_id).order_by(System.name, System.id)):
                    lines.append(LineIn(description=f"Monitoring, {month_name(period)}: {system.name}", unit_cents=monthly or 0,
                                        system_id=system.id))
                    missing = missing or monthly is None
            if not lines:
                skipped.append({"customer": who_, "reason": "Nothing to bill."})
                continue
            inv = Invoice(customer_id=c.id, period=period)
            s.add(inv)
            s.flush()
            set_lines(s, inv, lines)
            created.append(inv)
        s.commit()
        return {"created": [invoice_row(s, inv) for inv in created], "skipped": skipped, "missing_prices": missing}

    @app.get("/api/office/invoices/{invoice_id}")
    def invoice(invoice_id: int, who=Depends(staff_only), s=Depends(db)):
        return detail(s, get_invoice(s, invoice_id))

    @app.patch("/api/office/invoices/{invoice_id}")
    def update_invoice(invoice_id: int, body: InvoicePatch, who=Depends(staff_only), s=Depends(db)):
        """Edit a draft's lines and notes; change the due date; move it along: draft -> sent -> paid, or void."""
        inv = get_invoice(s, invoice_id)
        fields = body.model_fields_set
        if ("lines" in fields and body.lines is not None) or "notes" in fields:
            if inv.status != "draft":
                raise HTTPException(409, "Only a draft can be changed. Move it back to draft first.")
            if body.lines is not None:
                set_lines(s, inv, body.lines)
            if "notes" in fields:
                inv.notes = _clean(body.notes)
        if "due" in fields:
            if inv.status in ("paid", "void"):
                raise HTTPException(409, "This invoice is closed.")
            inv.due = body.due
        if body.status is not None and body.status != inv.status:
            if body.status not in MOVES[inv.status]:
                raise HTTPException(409, f"A {inv.status} invoice can't be marked {body.status}.")
            if body.status in ("sent", "paid"):
                s.flush()
                issue(s, inv)
            if body.status == "paid":
                if body.paid_on is not None and body.paid_on > dt.date.today():
                    raise HTTPException(422, "The paid date can't be in the future.")
                inv.paid_on = body.paid_on or dt.date.today()
            elif inv.status == "paid":                                  # paid by mistake
                inv.paid_on = inv.paid_note = None
            inv.status = body.status
        if "paid_note" in fields and inv.status == "paid":
            inv.paid_note = _clean(body.paid_note)
        s.commit()
        return detail(s, inv)

    @app.post("/api/office/invoices/{invoice_id}/send")
    def send(invoice_id: int, who=Depends(staff_only), s=Depends(db)):
        """Email the invoice to the customer (again, if it was sent before)."""
        inv = get_invoice(s, invoice_id)
        if inv.status not in ("draft", "sent"):
            raise HTTPException(409, f"This invoice is {inv.status}.")
        c = s.get(Customer, inv.customer_id)
        if not c.email:
            raise HTTPException(422, "The customer has no email address. Add one on the Customers page.")
        issue(s, inv)
        if not send_mail(c.email, f"Fullscope invoice {1000 + inv.id} for {c.company}", email_text(s, inv)):
            s.rollback()
            raise HTTPException(503, "Email is off on the server, so it wasn't sent. Print it instead, then mark it sent.")
        inv.status, inv.sent_at, inv.emailed = "sent", utcnow(), True
        s.commit()
        return detail(s, inv)

    @app.delete("/api/office/invoices/{invoice_id}")
    def delete_invoice(invoice_id: int, who=Depends(staff_only), s=Depends(db)):
        """Throw away a draft. Sent invoices are voided instead, so their numbers stay accounted for."""
        inv = get_invoice(s, invoice_id)
        if inv.status != "draft":
            raise HTTPException(409, "Only a draft can be deleted. Void it instead.")
        for x in lines_of(s, inv):
            s.delete(x)
        s.delete(inv)
        s.commit()
        return {"ok": True}
