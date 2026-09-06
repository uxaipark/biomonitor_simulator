"""Config change history + run snapshots + restore through the DB layer."""
import json
import time

from emulator.config import Config
from emulator.db import DB


def test_config_changes_are_recorded_and_restorable(tmp_path):
    cfg = Config(tmp_path / "config.json")
    db = DB(tmp_path / "t.db")
    got = []
    cfg.add_listener(lambda changes, source: (got.append((changes, source)), db.add_config_changes(time.time(), source, None, changes)))
    cfg.update({"general": {"active_patients": 321}, "scenario": {"network": {"enabled": True, "intensity": 55}}}, source="gui")
    cfg.update({"general": {"active_patients": 321}}, source="gui")          # no-op: nothing recorded
    assert len(got) == 1 and got[0][1] == "gui"
    paths = {c[0] for c in got[0][0]}
    assert {"general.active_patients", "scenario.network.enabled", "scenario.network.intensity"} <= paths
    h = db.config_history(limit=10)
    assert h["total"] == 3 and h["items"][0]["source"] == "gui"
    row = next(i for i in h["items"] if i["path"] == "general.active_patients")
    assert row["old"] == 120 and row["new"] == 321
    # run snapshot + restore of a single change
    rid = db.start_run("-:9100", 3, 321, 10, json.dumps(cfg.snapshot()))
    assert db.run_config(rid)["general"]["active_patients"] == 321
    assert db.runs()[0]["has_config"] == 1
    cfg.update({"general": {"active_patients": 50}}, source="api")
    undo = db.config_change(row["id"])
    cfg.update({"general": {"active_patients": undo["old"]}}, source="restore")
    assert cfg.get("general", "active_patients") == 120
    assert db.config_history(limit=1)["items"][0]["source"] == "restore"
    # reset is recorded too
    cfg.reset(source="reset:all")
    assert db.config_history(limit=5)["items"][0]["source"] == "reset:all"
