"""Admin commands for the cloud database.

  python manage.py init-db
  python manage.py create-account "Tyler" tyler@example.com
  python manage.py create-key 1                      (prints the API key once)
  python manage.py create-system 1 "Home" home --refrigerant R-410A --atm-psia 14.0
  python manage.py list
  python manage.py set-email 1 you@gmail.com        (where alert emails go)
  python manage.py backup                            (copy dev.db into backups/, keeps 7)
  python manage.py prune                             (thin data older than 30 days, drop older than 365)
  python manage.py test-email 1                      (needs SMTP_* in .env)
  python manage.py create-user you@gmail.com --name "Tyler" --account 1        (contractor; asks for a password)
  python manage.py create-user owner@example.com --role homeowner --system 1   (sees system 1 only)
  python manage.py set-password you@gmail.com
"""
import argparse
import getpass
import sys

from sqlalchemy import select

from hvaccloud import auth, maintenance
from hvaccloud.alerts import Mailer
from hvaccloud.db import (Account, ApiKey, Device, System, SystemMember, User, init_db, make_engine,
                          session_factory)
from hvaccloud.refrigerants import FLUIDS


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create tables (and TimescaleDB hypertables on PostgreSQL)")
    a = sub.add_parser("create-account")
    a.add_argument("name")
    a.add_argument("email")
    k = sub.add_parser("create-key")
    k.add_argument("account_id", type=int)
    y = sub.add_parser("create-system")
    y.add_argument("account_id", type=int)
    y.add_argument("name")
    y.add_argument("site_id", help="SITE_ID in the nodes' config.h")
    y.add_argument("--refrigerant", default="R-410A", choices=list(FLUIDS))
    y.add_argument("--atm-psia", type=float, default=14.7, help="about 14.0 at 1,200 ft")
    y.add_argument("--no-heat-pump", action="store_true")
    y.add_argument("--ob", choices=["cool", "heat"], default="cool", help="cool = O terminal, heat = B")
    sub.add_parser("list")
    m = sub.add_parser("set-email", help="change an account's email (alerts are sent there)")
    m.add_argument("account_id", type=int)
    m.add_argument("email")
    sub.add_parser("backup", help="copy the SQLite database into BACKUP_DIR (keeps the newest BACKUP_KEEP)")
    sub.add_parser("prune", help="average snapshots older than FULL_DETAIL_DAYS per minute, delete older than KEEP_DAYS")
    u = sub.add_parser("create-user", help="add a person who signs in with email and password")
    u.add_argument("email")
    u.add_argument("--name")
    u.add_argument("--role", choices=["contractor", "homeowner"], default="contractor")
    u.add_argument("--account", type=int, help="contractor's account id")
    u.add_argument("--system", type=int, action="append", default=[], help="homeowner's system id (repeatable)")
    u.add_argument("--no-password", action="store_true", help="don't ask now; set it later with set-password")
    pw = sub.add_parser("set-password", help="set or reset a user's password")
    pw.add_argument("email")
    e = sub.add_parser("test-email", help="send a test alert email to an account (checks the SMTP_* settings)")
    e.add_argument("account_id", type=int)
    args = ap.parse_args(argv)

    engine = make_engine()
    init_db(engine)
    if args.cmd == "backup":
        path = maintenance.backup()
        sys.exit(0 if path else "backup skipped: the database is not a SQLite file (use pg_dump)")
    if args.cmd == "prune":
        print(maintenance.prune(session_factory(engine)))
        return
    password = None
    if args.cmd == "set-password" or (args.cmd == "create-user" and not args.no_password):
        password = ask_password()
    with session_factory(engine)() as s, s.begin():
        if args.cmd == "init-db":
            print("database ready:", engine.url.render_as_string(hide_password=True))
        elif args.cmd == "create-account":
            acct = Account(name=args.name, email=args.email)
            s.add(acct)
            s.flush()
            print(f"account {acct.id}: {acct.name} <{acct.email}>")
        elif args.cmd == "create-key":
            if s.get(Account, args.account_id) is None:
                sys.exit(f"no account {args.account_id}")
            row, key = ApiKey.new(args.account_id)
            s.add(row)
            print(f"API key for account {args.account_id} (shown once, store it safely):\n{key}")
        elif args.cmd == "create-system":
            if s.get(Account, args.account_id) is None:
                sys.exit(f"no account {args.account_id}")
            if s.scalar(select(System).where(System.site_id == args.site_id)):
                sys.exit(f"site id {args.site_id!r} is already used")
            system = System(account_id=args.account_id, name=args.name, site_id=args.site_id,
                            refrigerant=args.refrigerant, atm_psia=args.atm_psia,
                            heat_pump=not args.no_heat_pump, ob_energized=args.ob)
            s.add(system)
            s.flush()
            print(f"system {system.id}: {system.name} (site {system.site_id!r}, {system.refrigerant})")
        elif args.cmd == "list":
            for acct in s.scalars(select(Account).order_by(Account.id)):
                print(f"account {acct.id}: {acct.name} <{acct.email}>")
                for system in acct.systems:
                    nodes = ", ".join(sorted(d.node for d in s.scalars(
                        select(Device).where(Device.system_id == system.id)))) or "no nodes yet"
                    print(f"  system {system.id}: {system.name} (site {system.site_id!r}, "
                          f"{system.refrigerant}) - {nodes}")
            for user in s.scalars(select(User).order_by(User.id)):
                where = (f"account {user.account_id}" if user.role == "contractor" else "systems " + ", ".join(
                    str(m.system_id) for m in s.scalars(select(SystemMember).where(SystemMember.user_id == user.id))))
                print(f"user {user.id}: {user.email} ({user.role}, {where})" + ("" if user.password_hash else " - no password"))
        elif args.cmd == "set-email":
            acct = s.get(Account, args.account_id)
            if acct is None:
                sys.exit(f"no account {args.account_id}")
            acct.email = args.email
            print(f"account {acct.id}: {acct.name} <{acct.email}>")
        elif args.cmd == "create-user":
            email = args.email.strip().lower()
            if s.scalar(select(User).where(User.email == email)):
                sys.exit(f"{email} already has a sign-in (use set-password to change it)")
            if args.role == "contractor":
                if args.account is None or s.get(Account, args.account) is None:
                    sys.exit("a contractor needs --account <id> of an existing account (see list)")
            elif not args.system:
                sys.exit("a homeowner needs at least one --system <id>")
            for sid in args.system:
                if s.get(System, sid) is None:
                    sys.exit(f"no system {sid}")
            user = User(email=email, name=args.name, role=args.role,
                        account_id=args.account if args.role == "contractor" else None,
                        password_hash=auth.hash_password(password) if password else None)
            s.add(user)
            s.flush()
            for sid in args.system:
                s.add(SystemMember(system_id=sid, user_id=user.id))
            print(f"user {user.id}: {email} ({args.role})" + ("" if password else " - no password yet"))
        elif args.cmd == "set-password":
            user = s.scalar(select(User).where(User.email == args.email.strip().lower()))
            if user is None:
                sys.exit(f"no user {args.email}")
            user.password_hash = auth.hash_password(password)
            print(f"password set for {user.email}")
        elif args.cmd == "test-email":
            acct = s.get(Account, args.account_id)
            if acct is None:
                sys.exit(f"no account {args.account_id}")
            mailer = Mailer()
            if not mailer.enabled:
                sys.exit("email is off: set SMTP_HOST, SMTP_USER and SMTP_PASS in hvac-cloud/.env")
            ok = mailer.send(acct.email, "[Fullscope] Test alert email",
                             "Alert emails from your Fullscope cloud are set up and working.")
            sys.exit(0 if ok else f"sending to {acct.email} failed; see the message above")


def ask_password():
    """Typed twice at a hidden prompt, so it never appears on screen or in shell history."""
    while True:
        first = getpass.getpass("New password (at least %d characters): " % auth.MIN_PASSWORD)
        problem = auth.password_problem(first)
        if problem:
            print(problem)
            continue
        if getpass.getpass("Same password again: ") != first:
            print("They don't match; try again.")
            continue
        return first


if __name__ == "__main__":
    main()
