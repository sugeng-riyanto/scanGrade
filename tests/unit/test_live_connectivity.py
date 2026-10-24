"""ScanMeet connectivity probe tests: no remote SDK, network or secrets."""
import asyncio
import sys
import types

from deploy import livekit_connectivity as check


def test_invalid_pilot_never_attempts_network(tmp_path, monkeypatch):
    conf = tmp_path / "pilot.env"
    conf.write_text("SCANGRADE_LIVE_ENABLED=true\nLIVEKIT_URL=ws://localhost\n")
    monkeypatch.setattr(check, "probe", lambda *_: (_ for _ in ()).throw(AssertionError("probe called")))
    assert check.main(["--env", str(conf)]) == 1


def test_probe_reads_only_one_room_and_uses_https(monkeypatch):
    results = []
    class FakeClient:
        def __init__(self, url, api_key, api_secret):
            results.append(("url", url, api_key, api_secret))
            self.room = types.SimpleNamespace(list_rooms=self.list_rooms)
        async def list_rooms(self, request):
            results.append(("list", request.names))
        async def aclose(self):
            results.append(("closed",))
    fake = types.ModuleType("livekit")
    fake.api = types.SimpleNamespace(
        LiveKitAPI=FakeClient,
        ListRoomsRequest=lambda names: types.SimpleNamespace(names=names),
    )
    monkeypatch.setitem(sys.modules, "livekit", fake)
    asyncio.run(check.probe({
        "LIVEKIT_URL": "wss://school.livekit.cloud",
        "LIVEKIT_API_KEY": "synthetic-key",
        "LIVEKIT_API_SECRET": "synthetic-secret",
    }))
    assert results[0] == ("url", "https://school.livekit.cloud", "synthetic-key", "synthetic-secret")
    assert results[1] == ("list", ["scangrade-staging-connectivity-probe"])
    assert results[2] == ("closed",)
