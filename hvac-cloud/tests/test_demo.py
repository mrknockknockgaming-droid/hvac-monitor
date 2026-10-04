from sqlalchemy import select

import demo
from hvaccloud.db import Account, Alert, Snapshot, System, User

EXPECTED = {"demo-garcia": set(), "demo-thompson": {"dt_low"}, "demo-patel": {"sh_high", "sc_low"},
            "demo-miller": {"ctoa_high"}, "demo-nguyen": {"node_offline"}, "demo-brooks": {"sh_low"}}


def test_setup_gives_each_home_its_fault_and_reset_removes_only_the_demo(sessions, seeded, tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "LOGIN_FILE", str(tmp_path / "demo-login.txt"))
    monkeypatch.setattr(demo, "BACKFILL_H", 1)                       # an hour is enough to raise the alerts
    monkeypatch.setattr(demo, "running", lambda home, t, i: home["on"] is None or home["scenario"] == "monitor_offline")
    demo.setup(sessions)
    with sessions() as s:
        acct = demo.demo_account(s)
        for sy in s.scalars(select(System).where(System.account_id == acct.id)):
            open_codes = {a.code for a in s.scalars(select(Alert).where(Alert.system_id == sy.id, Alert.raised_at.is_not(None),
                                                                        Alert.cleared_at.is_(None)))}
            assert open_codes == EXPECTED[sy.site_id], sy.site_id
        assert s.scalar(select(User).where(User.email == demo.TECH_LOGIN)).account_id == acct.id
    login = (tmp_path / "demo-login.txt").read_text()
    assert demo.TECH_LOGIN in login and demo.HOMEOWNER_LOGIN in login

    demo.reset(sessions)
    with sessions() as s:
        assert demo.demo_account(s) is None
        assert s.scalar(select(User).where(User.email.like("%fullscope.example"))) is None
        assert [a.name for a in s.scalars(select(Account).order_by(Account.id))] == ["Tyler", "Other"]   # untouched
        assert [x.site_id for x in s.scalars(select(System).order_by(System.id))] == ["home", "other"]
        assert s.scalar(select(Snapshot.time)) is None
    assert not (tmp_path / "demo-login.txt").exists()
