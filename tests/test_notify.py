"""Notification fan-out and the console/log channel."""
from __future__ import annotations

from fa.models import Alert, Signal
from fa.notify.console import ConsoleChannel
from fa.notify.dispatcher import Dispatcher

SIGNAL = Signal(alert=Alert(id=1, ticker="PODD", kind="pct_up"), title="titulo", message="mensaje")


class StubChannel:
    def __init__(self, name: str, *, usable: bool = True, ok: bool = True, boom: bool = False) -> None:
        self.name = name
        self._usable = usable
        self._ok = ok
        self._boom = boom

    def available(self) -> bool:
        return self._usable

    def send(self, signal: Signal) -> bool:
        if self._boom:
            raise RuntimeError("channel exploded")
        return self._ok


def test_dispatcher_lists_only_available_channels():
    dispatcher = Dispatcher([StubChannel("a"), StubChannel("b", usable=False)])
    assert dispatcher.names == ("a",)


def test_dispatcher_reports_successful_channels():
    dispatcher = Dispatcher([StubChannel("a"), StubChannel("b", ok=False)])
    assert dispatcher.send(SIGNAL) == ["a"]


def test_a_crashing_channel_does_not_stop_the_others():
    dispatcher = Dispatcher([StubChannel("boom", boom=True), StubChannel("good")])
    assert dispatcher.send(SIGNAL) == ["good"]


def test_console_channel_appends_to_the_log(tmp_path, capsys):
    log_path = tmp_path / "alerts.log"
    assert ConsoleChannel(log_path).send(SIGNAL) is True
    assert "PODD" in log_path.read_text(encoding="utf-8")
    assert "titulo" in capsys.readouterr().out


def test_console_channel_can_stay_silent(tmp_path, capsys):
    ConsoleChannel(tmp_path / "alerts.log", echo=False).send(SIGNAL)
    assert capsys.readouterr().out == ""
