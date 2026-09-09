"""Offline tests for the ComSmartSEM init logic.

These use a fake control object injected via dispatch_factory, so the remoting/local
selection and the InitialiseRemoting/InitialiseRemote fallback are exercised WITHOUT
pywin32. The real VARIANT marshalling remains instrument-untested.
"""

import pytest

from gisdep.api.com import ComSmartSEM
from gisdep.api.interface import SmartSEMError


class FakeCzApi:
    """Configurable stand-in for the CZEMApi ActiveX control."""

    def __init__(self, has=("InitialiseRemoting",), rc=0):
        # which init methods "exist"; each existing one returns `rc` when called
        self._has = set(has)
        self._rc = rc
        self.calls = []

    def _maybe(self, name):
        if name not in self._has:
            raise AttributeError(name)
        self.calls.append(name)
        return self._rc

    # dynamic-ish dispatch: only the configured methods resolve
    def __getattr__(self, name):
        if name in ("InitialiseRemoting", "InitialiseRemote", "Initialise", "ClosingControl"):
            def _call(*args):
                if name == "ClosingControl":
                    self.calls.append(name)
                    return 0
                if name == "Initialise":
                    self.calls.append(("Initialise", args))
                    return self._rc
                return self._maybe(name)
            return _call
        raise AttributeError(name)


def backend(fake, **kw):
    return ComSmartSEM(dispatch_factory=lambda: fake, **kw)


def test_default_mode_is_remoting():
    assert ComSmartSEM().__dict__["_init_mode"] == "remoting"


def test_remoting_uses_initialiseremoting_when_present():
    fake = FakeCzApi(has=("InitialiseRemoting",))
    b = backend(fake)
    b.initialise()
    assert "InitialiseRemoting" in fake.calls
    assert b._init_method_used == "InitialiseRemoting"


def test_remoting_falls_back_to_initialiseremote():
    # Some controls expose the C++ name 'InitialiseRemote' instead.
    fake = FakeCzApi(has=("InitialiseRemote",))
    b = backend(fake)
    b.initialise()
    assert fake.calls == ["InitialiseRemote"]
    assert b._init_method_used == "InitialiseRemote"


def test_remoting_nonzero_rc_raises_without_fallback():
    # If InitialiseRemoting exists but returns an error rc, that is a real failure --
    # do NOT silently fall through to InitialiseRemote.
    fake = FakeCzApi(has=("InitialiseRemoting", "InitialiseRemote"), rc=2027)
    b = backend(fake)
    with pytest.raises(SmartSEMError) as exc:
        b.initialise()
    assert exc.value.code == 2027
    assert fake.calls == ["InitialiseRemoting"]  # never tried the second name


def test_remoting_no_method_raises_runtime():
    fake = FakeCzApi(has=())  # neither remoting method exists
    b = backend(fake)
    with pytest.raises(RuntimeError):
        b.initialise()


def test_local_mode_uses_initialise():
    fake = FakeCzApi(has=("Initialise",))
    b = backend(fake, init_mode="local", machine="SEM")
    b.initialise()
    assert ("Initialise", ("SEM",)) in fake.calls
    assert b._init_method_used == "Initialise"


def test_local_mode_nonzero_rc_raises():
    fake = FakeCzApi(has=("Initialise",), rc=1019)
    b = backend(fake, init_mode="local")
    with pytest.raises(SmartSEMError) as exc:
        b.initialise()
    assert exc.value.code == 1019


def test_bad_init_mode_rejected():
    with pytest.raises(ValueError):
        ComSmartSEM(init_mode="nonsense")


def test_close_calls_closingcontrol_and_is_idempotent():
    fake = FakeCzApi(has=("InitialiseRemoting",))
    b = backend(fake)
    b.initialise()
    b.close()
    assert "ClosingControl" in fake.calls
    b.close()  # second call is a no-op, must not raise
