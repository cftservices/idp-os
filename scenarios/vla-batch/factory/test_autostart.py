"""SetAutostart: with self-chaining off a COMPLETE factory waits for the MES (demo pause)."""
import importlib
import os
import sys


class FakeProcess:
    def __init__(self):
        self.state = "COMPLETE"
        self.started = []

    def tick(self, dt):
        pass

    def start_batch(self, recipe_id):
        self.started.append(recipe_id)
        self.state = "DOSING"   # a started batch is no longer COMPLETE
        return 0


def _server(monkeypatch):
    monkeypatch.setenv("AUTOSTART_RECIPE", "chocolate-vla-1L")
    monkeypatch.setenv("AUTOSTART_DELAY_S", "1")
    sys.path.insert(0, os.path.dirname(__file__))
    sys.modules.pop("server", None)
    srv = importlib.import_module("server")
    p = FakeProcess()
    s = srv.VlaServer(p)
    s._elapsed, s._complete_since = 0.0, None   # normally set when the run loop starts
    return srv, s, p


def _run(srv, s, seconds):
    for _ in range(int(seconds / srv.TICK_INTERVAL) + 1):
        s._step()


def test_complete_factory_chains_by_default(monkeypatch):
    srv, s, p = _server(monkeypatch)
    _run(srv, s, 2)
    assert p.started == ["chocolate-vla-1L"]


def test_autostart_off_holds_the_line_until_it_is_on_again(monkeypatch):
    srv, s, p = _server(monkeypatch)
    s.autostart = False
    _run(srv, s, 5)
    assert p.started == []
    s.autostart = True
    _run(srv, s, 2)
    assert p.started == ["chocolate-vla-1L"]
