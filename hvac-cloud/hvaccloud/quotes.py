"""Quotes (Fullscope Office): a price offer that staff email to a customer or lead with a link to accept it.

A quote has lines like an invoice. Sending it (or "Get link", to paste into a text) makes a new private
link, {APP_URL}quote.html#/quote/<token>, and an older link stops working; it is issued today and can be
answered until valid_until (QUOTE_DAYS later). The customer opens the page, types their name and accepts,
or declines with a reason; ORDER_EMAIL gets an email either way. An accepted quote becomes a draft
invoice with "Make invoice". Staff can also mark a quote accepted or declined by hand (a yes on the phone).
Amounts are whole cents.

Mounted by api.create_app, which hands over its database session, sign-in and email helpers.
"""
import datetime as dt
import secrets
from typing import Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import auth, settings
from .billing import LineIn, money
from .db import Customer, Invoice, InvoiceLine, Quote, QuoteLine, as_utc, hash_key, utcnow

STATUSES = ("draft", "sent", "accepted", "declined", "void")
MOVES = {"draft": ("void",), "sent": ("draft", "accepted", "declined", "void"), "accepted": ("sent",),
         "declined": ("draft", "void"), "void": ("draft",)}
BAD_LINK = "This link doesn't work. A newer one may have been sent; ask Fullscope for the latest."


class QuoteIn(BaseModel):
    customer_id: int
    lines: list[LineIn] = Field([], max_length=200)
    notes: str | None = Field(None, max_length=2000)


class QuotePatch(BaseModel):
    lines: list[LineIn] | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)
    valid_until: dt.date | None = None
    status: Literal["draft", "sent", "accepted", "declined", "void"] | None = None
    answered_name: str | None = Field(None, max_length=120)              # who said yes or no, when marked by hand
    decline_reason: str | None = Field(None, max_length=1000)


class SendIn(BaseModel):
    email: bool = True                                                   # False: only make a link to paste


class TokenIn(BaseModel):
    token: str = Field(max_length=100)


class AnswerIn(BaseModel):
    token: str = Field(max_length=100)
    accept: bool
    name: str | None = Field(None, max_length=120)
    reason: str | None = Field(None, max_length=1000)


def _iso(t):
    return as_utc(t).isoformat() if t else None


def _day(d):
    return d.isoformat() if d else None


def _clean(v):
    v = (v or "").strip()
    return v or None


def number(q):
    return f"Q-{1000 + q.id}"


def lines_of(s, q):
    return s.scalars(select(QuoteLine).where(QuoteLine.quote_id == q.id).order_by(QuoteLine.id)).all()


def expired(q):
    return q.status == "sent" and q.valid_until is not None and q.valid_until < dt.date.today()


def quote_row(s, q, customer=None):
    c = customer or s.get(Customer, q.customer_id)
    lines = lines_of(s, q)
    return {"id": q.id, "number": number(q), "status": q.status, "customer": {"id": c.id, "company": c.company},
            "created_at": _iso(q.created_at), "issued": _day(q.issued), "valid_until": _day(q.valid_until), "expired": expired(q),
            "viewed_at": _iso(q.viewed_at), "answered_at": _iso(q.answered_at), "invoice_id": q.invoice_id,
            "total_cents": sum(x.qty * x.unit_cents for x in lines), "lines": len(lines)}


def summary(s):
    """Counts for the Office header: drafts, sent and waiting, accepted and not invoiced yet."""
    out = {"draft": 0, "sent": 0, "accepted": 0}
    for q in s.scalars(select(Quote).where(Quote.status.in_(("draft", "sent", "accepted")))):
        if q.status != "accepted" or q.invoice_id is None:
            out[q.status] += 1
    return out


def customer_quotes(s, c):
    rows = s.scalars(select(Quote).where(Quote.customer_id == c.id).order_by(Quote.id.desc()))
    return [quote_row(s, q, c) for q in rows]


def mount(app, db, principal, send_mail):

    def staff_only(who=Depends(principal)):
        if not who.staff:
            raise HTTPException(403, "for Fullscope staff only")
        return who

    def get_quote(s, quote_id):
        q = s.get(Quote, quote_id)
        if q is None:
            raise HTTPException(404, "no such quote")
        return q

    def line_views(lines):
        return [{"id": x.id, "description": x.description, "qty": x.qty, "unit_cents": x.unit_cents,
                 "amount_cents": x.qty * x.unit_cents} for x in lines]

    def detail(s, q):
        c = s.get(Customer, q.customer_id)
        return {**quote_row(s, q, c), "notes": q.notes, "sent_at": _iso(q.sent_at), "emailed": q.emailed,
                "answered_name": q.answered_name, "decline_reason": q.decline_reason,
                "has_link": q.token_hash is not None, "from_email": settings.ORDER_EMAIL or None,
                "to": {"company": c.company, "contact": c.contact, "email": c.email, "address": c.address},
                "lines": line_views(lines_of(s, q))}

    def public(s, q):
        """What the customer sees: not when they opened it, or whether it was emailed."""
        c = s.get(Customer, q.customer_id)
        lines = lines_of(s, q)
        return {"number": number(q), "status": q.status, "issued": _day(q.issued), "valid_until": _day(q.valid_until),
                "expired": expired(q), "answered_at": _iso(q.answered_at), "answered_name": q.answered_name,
                "to": {"company": c.company, "contact": c.contact, "address": c.address}, "notes": q.notes,
                "from_email": settings.ORDER_EMAIL or None, "lines": line_views(lines),
                "total_cents": sum(x.qty * x.unit_cents for x in lines)}

    def check_lines(lines):
        for x in lines:
            if not x.description.strip():
                raise HTTPException(422, "Every line needs a description.")

    def set_lines(s, q, lines):
        for old in lines_of(s, q):
            s.delete(old)
        s.flush()
        for x in lines:
            s.add(QuoteLine(quote_id=q.id, description=x.description.strip(), qty=x.qty, unit_cents=x.unit_cents))

    def new_link(q):
        """A fresh link; the one sent before stops working."""
        token = secrets.token_urlsafe(24)
        q.token_hash = hash_key(token)
        return f"{settings.APP_URL}quote.html#/quote/{token}"

    def email_text(s, q, link):
        c = s.get(Customer, q.customer_id)
        lines = lines_of(s, q)
        out = [f"Quote {number(q)} from Fullscope", f"Date: {_day(q.issued)}", f"Valid until: {_day(q.valid_until)}", "",
               f"For: {c.company}"]
        out += [v for v in (c.contact, c.address) if v]
        out.append("")
        out += [f"{x.description}: {x.qty} x {money(x.unit_cents)} = {money(x.qty * x.unit_cents)}" for x in lines]
        out += ["", f"Total: {money(sum(x.qty * x.unit_cents for x in lines))}"]
        if q.notes:
            out += ["", q.notes]
        out += ["", "To accept this quote, open this link:", link, "", "Questions? Reply to this email."]
        return "\n".join(out)

    def by_token(s, token):
        q = s.scalar(select(Quote).where(Quote.token_hash == hash_key(token))) if token else None
        if q is None:
            raise HTTPException(404, BAD_LINK)
        return q

    def tell_staff(s, q):
        if not settings.ORDER_EMAIL:
            return
        c = s.get(Customer, q.customer_id)
        total = money(sum(x.qty * x.unit_cents for x in lines_of(s, q)))
        if q.status == "accepted":
            subject = f"[Fullscope] Quote {number(q)} accepted by {c.company}"
            lines = [f"{q.answered_name} accepted quote {number(q)} for {c.company} ({total})."]
        else:
            subject = f"[Fullscope] Quote {number(q)} declined by {c.company}"
            lines = [f"{c.company} declined quote {number(q)} ({total}).", "", f"Reason: {q.decline_reason or '-'}"]
        lines += ["", f"Open it: {settings.APP_URL}office.html#/quotes/{q.id}"]
        send_mail(settings.ORDER_EMAIL, subject, "\n".join(lines))

    # ---------- staff ----------
    @app.get("/api/office/quotes")
    def quotes(status: Literal["open", "draft", "sent", "accepted", "declined", "void", "all"] = "open",
               who=Depends(staff_only), s=Depends(db)):
        """Quotes, newest first. "open" = drafts, sent ones waiting for an answer and accepted ones not invoiced."""
        q = select(Quote)
        if status == "open":
            q = q.where(Quote.status.in_(("draft", "sent", "accepted")))
        elif status != "all":
            q = q.where(Quote.status == status)
        rows = [x for x in s.scalars(q.order_by(Quote.id.desc()).limit(500))
                if status != "open" or x.status != "accepted" or x.invoice_id is None]
        customers = {c.id: c for c in s.scalars(select(Customer).where(Customer.id.in_({x.customer_id for x in rows})))}
        return [quote_row(s, x, customers[x.customer_id]) for x in rows]

    @app.post("/api/office/quotes")
    def add_quote(body: QuoteIn, who=Depends(staff_only), s=Depends(db)):
        """A draft for one customer or lead, empty or with the lines given."""
        if s.get(Customer, body.customer_id) is None:
            raise HTTPException(404, "no such customer")
        check_lines(body.lines)
        q = Quote(customer_id=body.customer_id, notes=_clean(body.notes))
        s.add(q)
        s.flush()
        set_lines(s, q, body.lines)
        s.commit()
        return detail(s, q)

    @app.get("/api/office/quotes/{quote_id}")
    def quote(quote_id: int, who=Depends(staff_only), s=Depends(db)):
        return detail(s, get_quote(s, quote_id))

    @app.patch("/api/office/quotes/{quote_id}")
    def update_quote(quote_id: int, body: QuotePatch, who=Depends(staff_only), s=Depends(db)):
        """Edit a draft; move the valid-until date; mark it answered by hand, back to draft, or void."""
        q = get_quote(s, quote_id)
        fields = body.model_fields_set
        if ("lines" in fields and body.lines is not None) or "notes" in fields:
            if q.status != "draft":
                raise HTTPException(409, "Only a draft can be changed. Move it back to draft first.")
            if body.lines is not None:
                check_lines(body.lines)
                set_lines(s, q, body.lines)
            if "notes" in fields:
                q.notes = _clean(body.notes)
        if "valid_until" in fields:
            if q.status not in ("draft", "sent"):
                raise HTTPException(409, f"This quote is {q.status}.")
            q.valid_until = body.valid_until
        if body.status is not None and body.status != q.status:
            if body.status not in MOVES[q.status]:
                raise HTTPException(409, f"This quote is {q.status}; it can't be marked {body.status}.")
            if q.status == "accepted" and q.invoice_id is not None:
                raise HTTPException(409, "It has an invoice already.")
            if body.status in ("accepted", "declined"):                 # the customer answered by phone or email
                q.answered_at, q.answered_ip = utcnow(), None
                q.answered_name = _clean(body.answered_name)
                q.decline_reason = _clean(body.decline_reason) if body.status == "declined" else None
            else:
                q.answered_at = q.answered_name = q.answered_ip = q.decline_reason = None
            if body.status in ("draft", "void"):                         # the link stops working
                q.token_hash = None
            q.status = body.status
        s.commit()
        return detail(s, q)

    @app.post("/api/office/quotes/{quote_id}/send")
    def send(quote_id: int, body: SendIn | None = None, who=Depends(staff_only), s=Depends(db)):
        """Email the quote with a new link (or only make the link, to paste into a text). Sending again
        replaces the link and keeps the dates."""
        q = get_quote(s, quote_id)
        if q.status not in ("draft", "sent"):
            raise HTTPException(409, f"This quote is {q.status}.")
        if not lines_of(s, q):
            raise HTTPException(422, "Add a line first.")
        c = s.get(Customer, q.customer_id)
        email = body is None or body.email
        if email and not c.email:
            raise HTTPException(422, "The customer has no email address. Add one on the Customers page, or use Get link.")
        if q.issued is None:
            q.issued = dt.date.today()
        if q.valid_until is None or q.valid_until < dt.date.today():
            q.valid_until = dt.date.today() + dt.timedelta(days=settings.QUOTE_DAYS)
        link = new_link(q)
        if email:
            if not send_mail(c.email, f"Fullscope quote {number(q)} for {c.company}", email_text(s, q, link)):
                s.rollback()
                raise HTTPException(503, "Email is off on the server, so it wasn't sent. Use Get link instead.")
            q.emailed = True
        q.status, q.sent_at = "sent", utcnow()
        s.commit()
        return {**detail(s, q), "link": link}

    @app.post("/api/office/quotes/{quote_id}/invoice")
    def make_invoice(quote_id: int, who=Depends(staff_only), s=Depends(db)):
        """A draft invoice with the accepted quote's lines."""
        q = get_quote(s, quote_id)
        if q.status != "accepted":
            raise HTTPException(409, "Only an accepted quote can become an invoice.")
        if q.invoice_id is not None:
            raise HTTPException(409, "It has an invoice already.")
        inv = Invoice(customer_id=q.customer_id, notes=f"Quote {number(q)}" + (f"\n{q.notes}" if q.notes else ""))
        s.add(inv)
        s.flush()
        for x in lines_of(s, q):
            s.add(InvoiceLine(invoice_id=inv.id, description=x.description, qty=x.qty, unit_cents=x.unit_cents))
        q.invoice_id = inv.id
        s.commit()
        return {**detail(s, q), "invoice_id": inv.id}

    @app.delete("/api/office/quotes/{quote_id}")
    def delete_quote(quote_id: int, who=Depends(staff_only), s=Depends(db)):
        """Throw away a draft. One that was sent is voided instead."""
        q = get_quote(s, quote_id)
        if q.status != "draft" or q.sent_at is not None:
            raise HTTPException(409, "Only a draft that was never sent can be deleted. Void it instead.")
        for x in lines_of(s, q):
            s.delete(x)
        s.delete(q)
        s.commit()
        return {"ok": True}

    # ---------- public: the customer's link ----------
    @app.post("/api/quote/view")
    def view(body: TokenIn, s=Depends(db)):
        """The quote behind a link (the token travels in the body, never in a URL the server logs)."""
        q = by_token(s, body.token)
        if q.status not in ("sent", "accepted", "declined"):
            raise HTTPException(404, BAD_LINK)
        if q.viewed_at is None:
            q.viewed_at = utcnow()
            s.commit()
        return public(s, q)

    @app.post("/api/quote/answer")
    def answer(body: AnswerIn, request: Request, s=Depends(db)):
        """Accept (with the name of the person accepting) or decline (with a reason, if they give one)."""
        q = by_token(s, body.token)
        if q.status in ("accepted", "declined"):
            raise HTTPException(409, f"This quote was {q.status} already.")
        if q.status != "sent":
            raise HTTPException(404, BAD_LINK)
        if expired(q):
            raise HTTPException(410, f"This quote expired on {_day(q.valid_until)}. Ask Fullscope for a new one.")
        name = _clean(body.name)
        if body.accept and not name:
            raise HTTPException(422, "Type your name to accept.")
        q.status = "accepted" if body.accept else "declined"
        q.answered_at, q.answered_name, q.answered_ip = utcnow(), name, auth.client_ip(request)[:64]
        q.decline_reason = None if body.accept else _clean(body.reason)
        s.commit()
        tell_staff(s, q)
        return public(s, q)
