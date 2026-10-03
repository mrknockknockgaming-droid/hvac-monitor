"""Admin commands for the cloud database.

  python manage.py init-db
  python manage.py create-account "Tyler" tyler@example.com
  python manage.py create-key 1                      (prints the API key once)
  python manage.py create-system 1 "Home" home --refrigerant R-410A --atm-psia 14.0
  python manage.py list
  python manage.py set-email 1 you@gmail.com        (where alert emails go)
  python manage.py test-email 1                      (needs SMTP_* in .env)
"""
import argparse
import sys

from sqlalchemy import select

from hvaccloud.alerts import Mailer
from hvaccloud.db import Account, ApiKey, Device, System, init_db, make_engine, session_factory
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
    e = sub.add_parser("test-email", help="send a test alert email to an account (checks the SMTP_* settings)")
    e.add_argument("account_id", type=int)
    args = ap.parse_args(argv)

    engine = make_engine()
    init_db(engine)
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
        elif args.cmd == "set-email":
            acct = s.get(Account, args.account_id)
            if acct is None:
                sys.exit(f"no account {args.account_id}")
            acct.email = args.email
            print(f"account {acct.id}: {acct.name} <{acct.email}>")
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


if __name__ == "__main__":
    main()
