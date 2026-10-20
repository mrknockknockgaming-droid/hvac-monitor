"""Kits, boards and warranty (Fullscope Office), and the contractor's QR claim.

A kit is one shipped Fullscope: an outdoor and an indoor board flashed with the same kit code
(FS-7K3QX). The box label carries a QR code for {APP_URL}#/claim/<kit code>-<claim code>. A
contractor scans it, signs in and names the system: the server creates the system under their
account with site_id = the kit code in lower case (the boards' MQTT topics), starts the boards'
warranty and invites the homeowner. Staff list kits and boards, mark kits shipped to a customer,
open warranty claims and release a kit claimed by mistake.

Mounted by api.create_app, which hands over its database session, sign-in and invite helpers.
"""
import calendar
import datetime as dt
import re
import secrets
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from . import auth, settings
from .db import Board, Customer, Device, Kit, System, User, WarrantyClaim, as_utc, utcnow

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"       # no 0/O, 1/I/L: read off a label without mistakes
KIT_RE = re.compile(r"^FS-[A-Z0-9]{3,10}$")
KINDS = ("outdoor", "indoor")
CLAIM_STATUSES = ("open", "replaced", "repaired", "denied")


class BoardIn(BaseModel):
    kind: Literal["outdoor", "indoor"]
    serial: str = Field(max_length=20)
    fw: str | None = Field(None, max_length=32)


class KitIn(BaseModel):
    code: str | None = Field(None, max_length=16)               # made up when left out
    hw_rev: str | None = Field(None, max_length=16)
    notes: str | None = Field(None, max_length=1000)
    boards: list[BoardIn] = Field(min_length=1, max_length=4)


class KitPatch(BaseModel):
    customer_id: int | None = Field(None, ge=0)                # 0 = none
    shipped: bool | None = None
    notes: str | None = Field(None, max_length=1000)


class WarrantyIn(BaseModel):
    problem: str = Field(min_length=1, max_length=2000)


class ClaimPatch(BaseModel):
    status: Literal["open", "replaced", "repaired", "denied"]
    resolution: str | None = Field(None, max_length=2000)
    replacement_serial: str | None = Field(None, max_length=20)   # status "replaced": the board sent instead


class CodeIn(BaseModel):
    code: str = Field(max_length=40)


class ClaimIn(CodeIn):
    system_name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(None, max_length=500)
    homeowner_email: str | None = Field(None, max_length=320)


def _iso(t):
    return as_utc(t).isoformat() if t else None


def _clean(v):
    v = (v or "").strip()
    return v or None


def add_months(d, n):
    """The same day n months later, or the month's last day when it is shorter (C#'s AddMonths)."""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def serial_of(raw):
    """A board serial (its MAC) as AA:BB:CC:DD:EE:FF, from any common spelling; None if it isn't one."""
    hexes = re.sub(r"[\s:.-]", "", raw or "").upper()
    if not re.fullmatch(r"[0-9A-F]{12}", hexes):
        return None
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2))


def random_code(n):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def split_code(raw):
    """'fs-7k3qx-abcd2345' (the QR's end, or typed off the label) -> ("FS-7K3QX", "ABCD2345")."""
    code = re.sub(r"\s", "", raw or "").upper()
    kit, _, claim = code.rpartition("-")
    return kit, claim


def warranty_view(b):
    if b.warranty_start is None:
        return {"start": None, "months": b.warranty_months, "ends": None, "active": None}
    ends = add_months(b.warranty_start, b.warranty_months or settings.WARRANTY_MONTHS)
    return {"start": b.warranty_start.isoformat(), "months": b.warranty_months, "ends": ends.isoformat(),
            "active": dt.date.today() <= ends}


def kit_status(k):
    return "installed" if k.claimed_at else "shipped" if k.shipped_at else "stock"


def mount(app, db, principal, clean_email, homeowner_access):
    claim_throttle = auth.Throttle(limit=10, window=15 * 60)        # wrong claim codes per person

    def staff_only(who=Depends(principal)):
        if not who.staff:
            raise HTTPException(403, "for Fullscope staff only")
        return who

    def claim_url(k):
        return f"{settings.APP_URL}#/claim/{k.code}-{k.claim_code}"

    def board_view(s, b, live=True):
        out = {"id": b.id, "serial": b.serial, "kind": b.kind, "hw_rev": b.hw_rev, "fw": b.fw, "status": b.status,
               "notes": b.notes, "created_at": _iso(b.created_at), "warranty": warranty_view(b), "kit_id": b.kit_id}
        if live:                                                    # the node as the cloud last heard from it
            d = s.scalar(select(Device).where(Device.mac == b.serial).order_by(Device.last_seen.desc()))
            out["seen"] = {"fw": d.fw, "last_seen": _iso(d.last_seen), "rssi": d.rssi} if d else None
        return out

    def claim_view(c, s):
        board = s.get(Board, c.board_id)
        repl = s.get(Board, c.replacement_id) if c.replacement_id else None
        return {"id": c.id, "board": {"id": board.id, "serial": board.serial, "kind": board.kind}, "opened_at": _iso(c.opened_at),
                "problem": c.problem, "status": c.status, "resolution": c.resolution, "closed_at": _iso(c.closed_at),
                "replacement": {"id": repl.id, "serial": repl.serial} if repl else None}

    def kit_row(s, k):
        boards = s.scalars(select(Board).where(Board.kit_id == k.id).order_by(Board.kind.desc(), Board.id)).all()
        cust = s.get(Customer, k.customer_id) if k.customer_id else None
        system = s.get(System, k.system_id) if k.system_id else None
        return {"id": k.id, "code": k.code, "status": kit_status(k), "hw_rev": k.hw_rev, "created_at": _iso(k.created_at),
                "shipped_at": _iso(k.shipped_at), "claimed_at": _iso(k.claimed_at),
                "customer": {"id": cust.id, "company": cust.company} if cust else None,
                "system": {"id": system.id, "name": system.name, "site_id": system.site_id, "address": system.address} if system else None,
                "boards": [{"id": b.id, "kind": b.kind, "serial": b.serial} for b in boards]}

    def kit_detail(s, k):
        boards = s.scalars(select(Board).where(Board.kit_id == k.id).order_by(Board.kind.desc(), Board.id)).all()
        ids = [b.id for b in boards]
        claims = s.scalars(select(WarrantyClaim).where(or_(WarrantyClaim.board_id.in_(ids), WarrantyClaim.replacement_id.in_(ids)))
                           .order_by(WarrantyClaim.id.desc())).all() if ids else []
        by = s.get(User, k.claimed_by) if k.claimed_by else None
        return {**kit_row(s, k), "notes": k.notes, "claim_url": claim_url(k), "site_id": k.code.lower(),
                "claimed_by": (by.name or by.email) if by else None,
                "boards": [board_view(s, b) for b in boards], "claims": [claim_view(c, s) for c in claims]}

    def get_kit(s, kit_id):
        k = s.get(Kit, kit_id)
        if k is None:
            raise HTTPException(404, "no such kit")
        return k

    # ---------- staff ----------
    @app.get("/api/office/kits")
    def kits(who=Depends(staff_only), s=Depends(db)):
        """Every kit, newest first."""
        return [kit_row(s, k) for k in s.scalars(select(Kit).order_by(Kit.created_at.desc(), Kit.id.desc()))]

    @app.post("/api/office/kits")
    def add_kit(body: KitIn, who=Depends(staff_only), s=Depends(db)):
        """Register a kit and its boards (by serial). The code is made up unless given."""
        if body.code is not None:
            code = body.code.strip().upper()
            if not KIT_RE.match(code):
                raise HTTPException(422, "A kit code looks like FS-7K3QX.")
        else:
            code = "FS-" + random_code(5)
            while s.scalar(select(Kit.id).where(Kit.code == code)) is not None:
                code = "FS-" + random_code(5)
        if s.scalar(select(Kit.id).where(Kit.code == code)) is not None:
            raise HTTPException(409, f"There is already a kit {code}.")
        if s.scalar(select(System.id).where(System.site_id == code.lower())) is not None:
            raise HTTPException(409, f"A system already reports as {code.lower()}.")
        kinds = [b.kind for b in body.boards]
        if len(set(kinds)) != len(kinds):
            raise HTTPException(422, "A kit has one board of each kind.")
        rows = []
        for b in body.boards:
            serial = serial_of(b.serial)
            if serial is None:
                raise HTTPException(422, f"{b.serial.strip()} isn't a board serial (12 hex digits, like 24:6F:28:AB:CD:EF).")
            board = s.scalar(select(Board).where(Board.serial == serial))
            if board is not None and board.kit_id is not None:
                raise HTTPException(409, f"Board {serial} is already in another kit.")
            rows.append((board, serial, b))
        k = Kit(code=code, claim_code=random_code(8), hw_rev=_clean(body.hw_rev), notes=_clean(body.notes))
        s.add(k)
        s.flush()
        for board, serial, b in rows:
            if board is None:
                board = Board(serial=serial, kind=b.kind)
                s.add(board)
            board.kind, board.kit_id, board.status = b.kind, k.id, "stock"
            board.hw_rev = k.hw_rev or board.hw_rev
            board.fw = _clean(b.fw) or board.fw
        s.commit()
        return kit_detail(s, k)

    @app.get("/api/office/kits/{kit_id}")
    def kit(kit_id: int, who=Depends(staff_only), s=Depends(db)):
        return kit_detail(s, get_kit(s, kit_id))

    @app.patch("/api/office/kits/{kit_id}")
    def update_kit(kit_id: int, body: KitPatch, who=Depends(staff_only), s=Depends(db)):
        """Ship it to a customer (or take that back), and notes."""
        k = get_kit(s, kit_id)
        fields = body.model_fields_set
        if "customer_id" in fields:
            if body.customer_id and s.get(Customer, body.customer_id) is None:
                raise HTTPException(404, "no such customer")
            k.customer_id = body.customer_id or None
        if "notes" in fields:
            k.notes = _clean(body.notes)
        if body.shipped is not None and k.claimed_at is None:
            k.shipped_at = (k.shipped_at or utcnow()) if body.shipped else None
            for b in s.scalars(select(Board).where(Board.kit_id == k.id, Board.status.in_(("stock", "shipped")))):
                b.status = "shipped" if body.shipped else "stock"
        s.commit()
        return kit_detail(s, k)

    @app.post("/api/office/kits/{kit_id}/new-code")
    def new_code(kit_id: int, who=Depends(staff_only), s=Depends(db)):
        """A new claim code (print a new label): the old QR stops working."""
        k = get_kit(s, kit_id)
        if k.claimed_at is not None:
            raise HTTPException(409, "This kit is already claimed.")
        k.claim_code = random_code(8)
        s.commit()
        return kit_detail(s, k)

    @app.post("/api/office/kits/{kit_id}/release")
    def release(kit_id: int, who=Depends(staff_only), s=Depends(db)):
        """Undo a claim made by mistake. The system it made stays with that contractor, renamed out of the
        kit's way (its site_id gets -old<id>), so the kit can be claimed again."""
        k = get_kit(s, kit_id)
        if k.claimed_at is None:
            raise HTTPException(409, "This kit isn't claimed.")
        system = s.get(System, k.system_id) if k.system_id else None
        if system is not None and system.site_id == k.code.lower():
            system.site_id = f"{system.site_id}-old{system.id}"
        k.claimed_at = k.claimed_by = k.account_id = k.system_id = None
        k.claim_code = random_code(8)
        for b in s.scalars(select(Board).where(Board.kit_id == k.id, Board.status == "installed")):
            b.status = "shipped" if k.shipped_at else "stock"
            b.warranty_start = None
        s.commit()
        return kit_detail(s, k)

    @app.get("/api/office/boards")
    def boards(q: str = Query("", max_length=20), who=Depends(staff_only), s=Depends(db)):
        """Boards whose serial contains q (any spelling), newest first; at most 100."""
        hexes = re.sub(r"[\s:.-]", "", q).upper()
        rows = s.scalars(select(Board).order_by(Board.created_at.desc(), Board.id.desc())).all()
        out = [b for b in rows if hexes in b.serial.replace(":", "")][:100]
        return [board_view(s, b, live=False) for b in out]

    @app.post("/api/office/boards/{board_id}/claims")
    def open_claim(board_id: int, body: WarrantyIn, who=Depends(staff_only), s=Depends(db)):
        """Open a warranty claim on a board (it is marked RMA until the claim is settled)."""
        b = s.get(Board, board_id)
        if b is None:
            raise HTTPException(404, "no such board")
        problem = body.problem.strip()
        if not problem:
            raise HTTPException(422, "Describe the problem.")
        c = WarrantyClaim(board_id=b.id, problem=problem)
        s.add(c)
        b.status = "rma"
        s.commit()
        return claim_view(c, s)

    @app.patch("/api/office/claims/{claim_id}")
    def settle_claim(claim_id: int, body: ClaimPatch, who=Depends(staff_only), s=Depends(db)):
        """Settle a warranty claim. "replaced": the replacement board takes the old one's place in the kit
        and keeps its warranty; the old board is retired."""
        c = s.get(WarrantyClaim, claim_id)
        if c is None:
            raise HTTPException(404, "no such claim")
        old = s.get(Board, c.board_id)
        if body.status == "replaced":
            serial = serial_of(body.replacement_serial or "")
            if serial is None:
                raise HTTPException(422, "Enter the replacement board's serial.")
            new = s.scalar(select(Board).where(Board.serial == serial))
            if new is not None and (new.id == old.id or new.kit_id is not None):
                raise HTTPException(409, f"Board {serial} is already in a kit.")
            if new is None:
                new = Board(serial=serial, kind=old.kind, hw_rev=old.hw_rev)
                s.add(new)
                s.flush()
            new.kit_id, new.kind = old.kit_id, old.kind
            new.warranty_start, new.warranty_months = old.warranty_start, old.warranty_months
            new.status = "installed" if old.warranty_start else "shipped" if old.kit_id else "stock"
            old.kit_id, old.status = None, "retired"
            c.replacement_id = new.id
        elif body.status in ("repaired", "denied") and old.status == "rma":
            kit = s.get(Kit, old.kit_id) if old.kit_id else None
            old.status = kit_status(kit) if kit else "stock"
        c.status = body.status
        if "resolution" in body.model_fields_set:
            c.resolution = _clean(body.resolution)
        c.closed_at = None if body.status == "open" else (c.closed_at or utcnow())
        s.commit()
        return claim_view(c, s)

    # ---------- contractors: the QR claim ----------
    def find_claim(s, who, raw):
        """The kit a claim code opens, or 404 (counted against wrong guesses)."""
        if not who.is_tech:
            raise HTTPException(403, "Only a contractor can set up a Fullscope. Ask your contractor.")
        key = f"u{who.user_id}" if who.user_id else f"a{who.account_id}"
        if claim_throttle.blocked(key):
            raise HTTPException(429, "Too many wrong codes. Try again in 15 minutes.")
        code, claim = split_code(raw)
        k = s.scalar(select(Kit).where(Kit.code == code)) if code else None
        if k is None or not secrets.compare_digest(k.claim_code, claim):
            claim_throttle.fail(key)
            raise HTTPException(404, "That code doesn't match a Fullscope kit. Check the label and try again.")
        return k

    def lookup_view(s, who, k):
        boards = s.scalars(select(Board).where(Board.kit_id == k.id).order_by(Board.kind.desc(), Board.id)).all()
        out = {"kit": k.code, "boards": [{"kind": b.kind, "serial": b.serial} for b in boards], "system": None}
        if k.claimed_at is None:
            out["status"] = "available"
        elif k.account_id == who.account_id:
            system = s.get(System, k.system_id) if k.system_id else None
            out["status"] = "yours"
            out["system"] = {"id": system.id, "name": system.name} if system else None
        else:
            out["status"] = "taken"
        return out

    def shipped_elsewhere(s, who, k):
        """A kit shipped to a customer with an account is theirs to claim."""
        cust = s.get(Customer, k.customer_id) if k.customer_id else None
        return cust is not None and cust.account_id is not None and cust.account_id != who.account_id

    @app.post("/api/claim/lookup")
    def claim_lookup(body: CodeIn, who=Depends(principal), s=Depends(db)):
        """What a scanned QR code is: the kit, its boards, and whether it can be set up."""
        k = find_claim(s, who, body.code)
        out = lookup_view(s, who, k)
        if out["status"] == "available" and shipped_elsewhere(s, who, k):
            out["status"] = "taken"
        return out

    @app.post("/api/claim")
    def claim(body: ClaimIn, who=Depends(principal), s=Depends(db)):
        """Set up a kit: a new system on the contractor's account, the boards' warranty starts, and the
        homeowner (if given) is invited."""
        k = find_claim(s, who, body.code)
        if k.claimed_at is not None or shipped_elsewhere(s, who, k):
            raise HTTPException(409, "This kit is already set up. Contact Fullscope if that's wrong.")
        name = body.system_name.strip()
        if not name:
            raise HTTPException(422, "Name the system, for example the homeowner's last name.")
        email = clean_email(body.homeowner_email) if _clean(body.homeowner_email) else None
        if email:
            user = s.scalar(select(User).where(User.email == email))
            if user is not None and user.role != "homeowner":
                raise HTTPException(409, "That email already has a contractor sign-in.")
        site = k.code.lower()
        if s.scalar(select(System.id).where(System.site_id == site)) is not None:
            raise HTTPException(409, f"A system already reports as {site}. Contact Fullscope.")
        system = System(account_id=who.account_id, name=name, site_id=site, address=_clean(body.address), atm_psia=14.7)
        s.add(system)
        s.flush()
        k.claimed_at, k.claimed_by, k.account_id, k.system_id = utcnow(), who.user_id, who.account_id, system.id
        today = dt.date.today()
        for b in s.scalars(select(Board).where(Board.kit_id == k.id, Board.status.in_(("stock", "shipped")))):
            b.status, b.warranty_start = "installed", today
            b.warranty_months = b.warranty_months or settings.WARRANTY_MONTHS
        cust = s.scalar(select(Customer).where(Customer.account_id == who.account_id).order_by(Customer.id))
        if k.customer_id is None and cust is not None:
            k.customer_id = cust.id
        s.commit()
        invite = homeowner_access(s, who, system, email) if email else None
        return {"system": {"id": system.id, "name": system.name, "site_id": system.site_id, "address": system.address},
                "kit": k.code, "invite": invite}
