import pytest

from ala.shop.store import MemoryStore, SqliteStore


@pytest.fixture(params=["memory", "sqlite"])
async def store(request, tmp_path):
    s = MemoryStore() if request.param == "memory" else SqliteStore(str(tmp_path / "t.sqlite"))
    yield s
    await s.close()


async def test_round_trip_and_overwrite(store):
    await store.put("session", "a", {"cart": [1], "name": "రవి"})
    assert (await store.get("session", "a"))["name"] == "రవి"
    await store.put("session", "a", {"cart": []})
    assert await store.get("session", "a") == {"cart": []}
    assert await store.get("session", "missing") is None


async def test_stock(store):
    await store.set_stock("royyalu", 3.5)
    assert await store.load_stock() == {"royyalu": 3.5}


async def test_forget_me_really_forgets(store):
    await store.save_profile("  Ravi ", {"avoid": ["bony"]})
    assert await store.load_profile("ravi") == {"avoid": ["bony"]}
    await store.forget_profile("RAVI")
    assert await store.load_profile("ravi") is None
