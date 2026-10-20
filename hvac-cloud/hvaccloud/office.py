"""Fullscope Office: the business side, for Fullscope's own staff (users.staff).

Inbox: every order request (landing page), contact message (landing page) and support request
(contractors, from the app) is a ticket. Staff reply by email from the page or add internal notes;
each new ticket is also emailed to ORDER_EMAIL. Customers: the contractor companies Fullscope sells
to, linked to their sign-in account once they have one.

Mounted by api.create_app, which hands over its database session, sign-in and email helpers.
"""
import datetime as dt
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import auth, settings
from .db import (Account, Customer, Device, Invite, Order, Snapshot, System, SystemMember, Ticket, TicketMessage, User, as_utc,
                 utcnow)

KINDS = ("order", "contact", "support")
STATUSES = ("new", "open", "waiting", "closed")
CUSTOMER_STATUSES = ("lead", "active", "paused", "closed")
TOPIC_WORD = {"sales": "Sales question", "support": "Support request", "other": "Question"}


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=320)
    phone: str | None = Field(None, max_length=40)
    company: str | None = Field(None, max_length=200)
    topic: Literal["sales", "support", "other"] = "other"
    message: str = Field(min_length=1, max_length=4000)
    website: str | None = Field(None, max_length=200)          # hidden on the form: only bots fill it in


class SupportIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    system_id: int | None = None


class TicketPatch(BaseModel):
    status: Literal["new", "open", "waiting", "closed"] | None = None
    customer_id: int | None = Field(None, ge=0)                # 0 unlinks


class MessageIn(BaseModel):
    kind: Literal["reply", "note"]
    body: str = Field(min_length=1, max_length=4000)


class CustomerIn(BaseModel):
    """New customer, or the fields to change (only the fields sent are changed)."""
    company: str | None = Field(None, min_length=1, max_length=200)
    contact: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=320)
    phone: str | None = Field(None, max_length=40)
    address: str | None = Field(None, max_length=500)
    status: Literal["lead", "active", "paused", "closed"] | None = None
    notes: str | None = Field(None, max_length=4000)


class AccountIn(BaseModel):
    email: str = Field(max_length=320)                         # the contractor's admin, invited to sign in


def _iso(t):
    return as_utc(t).isoformat() if t else None


def _clean(v):
    v = (v or "").strip()
    return v or None


def mount(app, db, principal, clean_email, send_mail, make_invite, invite_view, stale, invite_days):
    contact_throttle = auth.Throttle(limit=5, window=60 * 60)      # landing-page contact messages per address per hour

    def staff_only(who=Depends(principal)):
        if not who.staff:
            raise HTTPException(403, "for Fullscope staff only")
        return who

    def notify(ticket):
        """New ticket: a copy to the business inbox (ORDER_EMAIL)."""
        if not settings.ORDER_EMAIL:
            return
        lines = [f"New {ticket.kind} #{ticket.id} in the Fullscope Office inbox.", "",
                 f"From: {ticket.name or '-'} <{ticket.email}>"]
        if ticket.company:
            lines.append(f"Company: {ticket.company}")
        if ticket.phone:
            lines.append(f"Phone: {ticket.phone}")
        lines += ["", ticket.body or "-", "", f"Open it: {settings.APP_URL}office.html#/inbox/{ticket.id}"]
        send_mail(settings.ORDER_EMAIL, f"[Fullscope #{ticket.id}] {ticket.subject}", "\n".join(lines))

    def customer_for_account(s, account_id):
        if account_id is None:
            return None
        return s.scalar(select(Customer.id).where(Customer.account_id == account_id).order_by(Customer.id))

    # ---------- public: the landing page's contact form ----------
    @app.post("/api/contact")
    def contact(body: ContactIn, request: Request, s=Depends(db)):
        """A question from the landing page: saved in the Office inbox and emailed to ORDER_EMAIL."""
        if body.website:                                           # a bot filled in the hidden field
            return {"ok": True}
        ip = auth.client_ip(request)
        if contact_throttle.blocked(ip):
            raise HTTPException(429, "Too many messages from this address. Try again in an hour.")
        email = clean_email(body.email)
        name, message = body.name.strip(), body.message.strip()
        if not name or not message:
            raise HTTPException(422, "Name and message are required.")
        contact_throttle.fail(ip)
        company = _clean(body.company)
        t = Ticket(kind="contact", subject=f"{TOPIC_WORD[body.topic]} from {company or name}", name=name, email=email,
                   phone=_clean(body.phone), company=company, body=message)
        s.add(t)
        s.commit()
        notify(t)
        return {"ok": True, "id": t.id}

    # ---------- contractors: support requests from the app ----------
    @app.post("/api/support")
    def ask_support(body: SupportIn, who=Depends(principal), s=Depends(db)):
        """A contractor asks Fullscope for help. Homeowners ask their contractor instead."""
        if not who.is_tech:
            raise HTTPException(403, "Please contact your heating and cooling contractor.")
        subject, message = body.subject.strip(), body.message.strip()
        if not subject or not message:
            raise HTTPException(422, "Subject and message are required.")
        system = None
        if body.system_id is not None:
            system = s.scalar(select(System).where(System.id == body.system_id, System.account_id == who.account_id))
            if system is None:
                raise HTTPException(404, "no such system")
        user = s.get(User, who.user_id) if who.user_id else None
        acct = s.get(Account, who.account_id)
        text = message + (f"\n\nSystem: {system.name} ({system.site_id})" if system else "")
        t = Ticket(kind="support", subject=subject, name=user.name if user else None, email=user.email if user else acct.email,
                   company=acct.name, body=text, account_id=acct.id, system_id=system.id if system else None,
                   user_id=user.id if user else None, customer_id=customer_for_account(s, acct.id))
        s.add(t)
        s.commit()
        notify(t)
        return support_view(s, t)

    @app.get("/api/support")
    def my_support(who=Depends(principal), s=Depends(db)):
        """The account's support requests, newest first, with Fullscope's replies (not internal notes)."""
        if not who.is_tech:
            raise HTTPException(403, "Please contact your heating and cooling contractor.")
        rows = s.scalars(select(Ticket).where(Ticket.kind == "support", Ticket.account_id == who.account_id)
                         .order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(50))
        return [support_view(s, t) for t in rows]

    def support_view(s, t):
        replies = s.scalars(select(TicketMessage).where(TicketMessage.ticket_id == t.id, TicketMessage.kind == "reply")
                            .order_by(TicketMessage.id))
        return {"id": t.id, "subject": t.subject, "body": t.body, "status": "closed" if t.status == "closed" else "open",
                "created_at": _iso(t.created_at), "name": t.name, "email": t.email,
                "replies": [{"body": m.body, "at": _iso(m.created_at)} for m in replies]}

    # ---------- staff: inbox ----------
    def ticket_row(s, t, customers):
        n = s.scalar(select(func.count()).select_from(TicketMessage).where(TicketMessage.ticket_id == t.id))
        c = customers.get(t.customer_id)
        return {"id": t.id, "kind": t.kind, "status": t.status, "subject": t.subject, "name": t.name, "email": t.email,
                "company": t.company, "created_at": _iso(t.created_at), "updated_at": _iso(t.updated_at), "messages": n,
                "customer": {"id": c.id, "company": c.company} if c else None}

    def get_ticket(s, ticket_id):
        t = s.get(Ticket, ticket_id)
        if t is None:
            raise HTTPException(404, "no such ticket")
        return t

    def ticket_detail(s, t):
        msgs = s.scalars(select(TicketMessage).where(TicketMessage.ticket_id == t.id).order_by(TicketMessage.id))
        authors = {}
        out = []
        for m in msgs:
            if m.author_id and m.author_id not in authors:
                u = s.get(User, m.author_id)
                authors[m.author_id] = (u.name or u.email) if u else None
            out.append({"id": m.id, "kind": m.kind, "body": m.body, "at": _iso(m.created_at), "emailed": bool(m.emailed),
                        "author": authors.get(m.author_id)})
        order = s.get(Order, t.order_id) if t.order_id else None
        customer = s.get(Customer, t.customer_id) if t.customer_id else None
        acct = s.get(Account, t.account_id) if t.account_id else None
        system = s.get(System, t.system_id) if t.system_id else None
        return {**ticket_row(s, t, {customer.id: customer} if customer else {}), "phone": t.phone, "body": t.body,
                "order": {"id": order.id, "systems": order.systems} if order else None,
                "account": {"id": acct.id, "name": acct.name} if acct else None,
                "system": {"id": system.id, "name": system.name, "site_id": system.site_id} if system else None,
                "thread": out}

    @app.get("/api/office/summary")
    def summary(who=Depends(staff_only), s=Depends(db)):
        """Counts for the Office header."""
        inbox = dict(s.execute(select(Ticket.status, func.count()).group_by(Ticket.status)).all())
        cust = dict(s.execute(select(Customer.status, func.count()).group_by(Customer.status)).all())
        return {"inbox": {k: inbox.get(k, 0) for k in STATUSES}, "customers": {k: cust.get(k, 0) for k in CUSTOMER_STATUSES}}

    @app.get("/api/office/inbox")
    def inbox(status: Literal["open", "closed", "all"] = "open", kind: Literal["order", "contact", "support", "all"] = "all",
              who=Depends(staff_only), s=Depends(db)):
        """Tickets, most recently active first. "open" = everything not closed."""
        q = select(Ticket)
        if status == "open":
            q = q.where(Ticket.status != "closed")
        elif status == "closed":
            q = q.where(Ticket.status == "closed")
        if kind != "all":
            q = q.where(Ticket.kind == kind)
        rows = s.scalars(q.order_by(Ticket.updated_at.desc(), Ticket.id.desc()).limit(500)).all()
        customers = {c.id: c for c in s.scalars(select(Customer).where(Customer.id.in_({t.customer_id for t in rows if t.customer_id})))}
        return [ticket_row(s, t, customers) for t in rows]

    @app.get("/api/office/inbox/{ticket_id}")
    def ticket(ticket_id: int, who=Depends(staff_only), s=Depends(db)):
        t = get_ticket(s, ticket_id)
        if t.status == "new":                                      # opened by staff: no longer new
            t.status = "open"
            s.commit()
        return ticket_detail(s, t)

    @app.patch("/api/office/inbox/{ticket_id}")
    def update_ticket(ticket_id: int, body: TicketPatch, who=Depends(staff_only), s=Depends(db)):
        t = get_ticket(s, ticket_id)
        if body.status is not None:
            t.status = body.status
        if "customer_id" in body.model_fields_set:
            if body.customer_id:
                if s.get(Customer, body.customer_id) is None:
                    raise HTTPException(404, "no such customer")
                t.customer_id = body.customer_id
            else:
                t.customer_id = None
        t.updated_at = utcnow()
        s.commit()
        return ticket_detail(s, t)

    @app.post("/api/office/inbox/{ticket_id}/messages")
    def add_message(ticket_id: int, body: MessageIn, who=Depends(staff_only), s=Depends(db)):
        """A reply (emailed to the sender, and shown to a contractor in the app) or an internal note."""
        t = get_ticket(s, ticket_id)
        text = body.body.strip()
        if not text:
            raise HTTPException(422, "Write a message first.")
        emailed = None
        if body.kind == "reply":
            emailed = send_mail(t.email, f"Re: {t.subject} [Fullscope #{t.id}]",
                                f"{text}\n\n--\nFullscope\n{settings.APP_URL}") or None
            if t.status in ("new", "open"):
                t.status = "waiting"
        s.add(TicketMessage(ticket_id=t.id, kind=body.kind, body=text, author_id=who.user_id, emailed=emailed))
        t.updated_at = utcnow()
        s.commit()
        return ticket_detail(s, t)

    @app.post("/api/office/inbox/{ticket_id}/customer")
    def ticket_to_customer(ticket_id: int, who=Depends(staff_only), s=Depends(db)):
        """Make a customer from the ticket's sender (an order request, usually) and link the two."""
        t = get_ticket(s, ticket_id)
        if t.customer_id:
            raise HTTPException(409, "This ticket already has a customer.")
        c = Customer(company=t.company or t.name or t.email, contact=t.name, email=t.email, phone=t.phone, status="lead")
        if t.account_id and customer_for_account(s, t.account_id) is None:
            c.account_id = t.account_id
        s.add(c)
        s.flush()
        t.customer_id = c.id
        t.updated_at = utcnow()
        s.commit()
        return ticket_detail(s, t)

    # ---------- staff: customers ----------
    def customer_row(s, c):
        systems = s.scalar(select(func.count()).select_from(System).where(System.account_id == c.account_id)) if c.account_id else 0
        open_t = s.scalar(select(func.count()).select_from(Ticket).where(Ticket.customer_id == c.id, Ticket.status != "closed"))
        return {"id": c.id, "company": c.company, "contact": c.contact, "email": c.email, "phone": c.phone,
                "status": c.status, "created_at": _iso(c.created_at), "account_id": c.account_id,
                "systems": systems, "open_tickets": open_t}

    def get_customer(s, customer_id):
        c = s.get(Customer, customer_id)
        if c is None:
            raise HTTPException(404, "no such customer")
        return c

    def customer_detail(s, c):
        out = {**customer_row(s, c), "address": c.address, "notes": c.notes, "account": None, "people": [], "invites": [],
               "systems_list": []}
        now = utcnow()
        if c.account_id:
            acct = s.get(Account, c.account_id)
            out["account"] = {"id": acct.id, "name": acct.name, "email": acct.email, "created_at": _iso(acct.created_at)}
            users = s.scalars(select(User).where(User.account_id == acct.id).order_by(User.email))
            out["people"] = [{"user_id": u.id, "email": u.email, "name": u.name, "admin": bool(u.admin),
                              "last_login": _iso(u.last_login)} for u in users]
            invites = s.scalars(select(Invite).where(Invite.account_id == acct.id, Invite.role == "contractor",
                                                     Invite.accepted_at.is_(None)).order_by(Invite.created_at.desc()))
            out["invites"] = [invite_view(i) for i in invites]
            for system in s.scalars(select(System).where(System.account_id == acct.id).order_by(System.name)):
                last = s.scalar(select(func.max(Snapshot.time)).where(Snapshot.system_id == system.id))
                devices = s.scalars(select(Device).where(Device.system_id == system.id)).all()
                online = sum(1 for d in devices if d.connected and d.last_seen and (now - as_utc(d.last_seen)).total_seconds() <= stale)
                homeowners = s.scalar(select(func.count()).select_from(SystemMember).where(SystemMember.system_id == system.id))
                out["systems_list"].append({"id": system.id, "name": system.name, "site_id": system.site_id,
                                            "last_reading": _iso(last), "nodes_online": online, "nodes": len(devices),
                                            "homeowners": homeowners})
        tickets = s.scalars(select(Ticket).where(Ticket.customer_id == c.id).order_by(Ticket.updated_at.desc(), Ticket.id.desc()))
        out["tickets"] = [ticket_row(s, t, {c.id: c}) for t in tickets]
        return out

    def apply_customer(c, body):
        fields = body.model_fields_set
        if "company" in fields:
            company = (body.company or "").strip()
            if not company:
                raise HTTPException(422, "The company name is required.")
            c.company = company
        for k in ("contact", "phone", "address", "notes"):
            if k in fields:
                setattr(c, k, _clean(getattr(body, k)))
        if "email" in fields:
            c.email = clean_email(body.email) if _clean(body.email) else None
        if "status" in fields and body.status is not None:
            c.status = body.status

    @app.get("/api/office/customers")
    def customers(who=Depends(staff_only), s=Depends(db)):
        return [customer_row(s, c) for c in s.scalars(select(Customer).order_by(Customer.company, Customer.id))]

    @app.post("/api/office/customers")
    def add_customer(body: CustomerIn, who=Depends(staff_only), s=Depends(db)):
        if not (body.company or "").strip():
            raise HTTPException(422, "The company name is required.")
        c = Customer(company=body.company.strip(), status="lead")
        apply_customer(c, body)
        s.add(c)
        s.commit()
        return customer_detail(s, c)

    @app.get("/api/office/customers/{customer_id}")
    def customer(customer_id: int, who=Depends(staff_only), s=Depends(db)):
        return customer_detail(s, get_customer(s, customer_id))

    @app.patch("/api/office/customers/{customer_id}")
    def update_customer(customer_id: int, body: CustomerIn, who=Depends(staff_only), s=Depends(db)):
        c = get_customer(s, customer_id)
        apply_customer(c, body)
        s.commit()
        return customer_detail(s, c)

    @app.post("/api/office/customers/{customer_id}/account")
    def make_account(customer_id: int, body: AccountIn, who=Depends(staff_only), s=Depends(db)):
        """Give the customer their Fullscope account and email its first admin an invite."""
        c = get_customer(s, customer_id)
        if c.account_id:
            raise HTTPException(409, "This customer already has an account.")
        email = clean_email(body.email)
        if s.scalar(select(User.id).where(User.email == email)) is not None:
            raise HTTPException(409, "That email already has a sign-in.")
        if s.scalar(select(Account.id).where(Account.email == email)) is not None:
            raise HTTPException(409, "That email already belongs to an account.")
        acct = Account(name=c.company, email=email)
        s.add(acct)
        s.flush()
        c.account_id = acct.id
        if c.status == "lead":
            c.status = "active"
        if not c.email:
            c.email = email
        for t in s.scalars(select(Ticket).where(Ticket.customer_id == c.id, Ticket.account_id.is_(None))):
            t.account_id = acct.id
        s.commit()
        inv, url = make_invite(s, who, email, "contractor", account_id=acct.id, admin=True)
        emailed = send_mail(email, f"Your Fullscope account for {c.company}",
                            f"Welcome to Fullscope. Your account for {c.company} is ready.\n\n"
                            f"Choose a password here (the link works for {invite_days} days):\n{url}\n\n"
                            "You'll be the account's admin: you can invite your technicians from the Account page.")
        return {**customer_detail(s, c), "invite": {"url": url, "emailed": emailed, "email": email}}
