from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.infra.storage import redis as redis_storage


class _FakePool:
    closed = False

    async def aclose(self) -> None:
        self.closed = True


class _FakeConnectionPoolFactory:
    @staticmethod
    def from_url(*args, **kwargs):
        return _FakePool()


class _FakeAsyncRedisModule:
    ConnectionPool = _FakeConnectionPoolFactory


class _FakeRedisWithClosablePool:
    def __init__(self, *, connection_pool) -> None:
        self.connection_pool = connection_pool

    async def aclose(self) -> None:
        return None


class _FakeLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:
        self.messages.append(message)


class _FakeRedisClient:
    def __init__(self, *, connection_pool, **kwargs) -> None:
        self.connection_pool = connection_pool


class _FakeScanRedisClient:
    async def keys(self, pattern: str) -> list[str]:
        raise AssertionError(f"Redis KEYS must not be used for pattern {pattern}")

    async def scan(
        self,
        cursor: int | str = 0,
        match: str | None = None,
        count: int | None = None,
    ) -> tuple[int, list[str]]:
        assert match == "task:*"
        assert count == 100
        if int(cursor) == 0:
            return 1, ["task:a", "task:b"]
        return 0, ["task:c"]


class _LargeFakeScanRedisClient:
    def __init__(self) -> None:
        self.calls = 0

    async def keys(self, pattern: str) -> list[str]:
        raise AssertionError(f"Redis KEYS must not be used for pattern {pattern}")

    async def scan(
        self,
        cursor: int | str = 0,
        match: str | None = None,
        count: int | None = None,
    ) -> tuple[int, list[str]]:
        assert match == "task:*"
        assert count == 100
        self.calls += 1
        start = int(cursor) * 100
        batch = [f"task:{index}" for index in range(start, start + 100)]
        next_cursor = int(cursor) + 1
        if next_cursor >= 20:
            return 0, batch
        return next_cursor, batch


class _FakeSetRedisClient:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, object, int | None]] = []

    async def set(self, key: str, value: object, ex: int | None = None) -> None:
        self.set_calls.append((key, value, ex))


class _FakeGetRedisClient:
    async def get(self, key: str) -> str:
        assert key == "cache:large"
        return '{"items": ["' + ("x" * 20_000) + '"]}'


class _FakeXaddRedisClient:
    def __init__(self) -> None:
        self.xadd_calls: list[tuple[str, dict[str, str], int | None]] = []

    async def xadd(
        self,
        stream_key: str,
        fields: dict[str, str],
        maxlen: int | None = None,
    ) -> str:
        self.xadd_calls.append((stream_key, fields, maxlen))
        return "1-0"


class _FakeStreamReadRedisClient:
    async def xrange(
        self,
        stream_key: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        assert stream_key == "stream:events"
        assert min == "-"
        assert max == "+"
        assert count == 10
        return [("1-0", {"data": '{"content": "' + ("x" * 20_000) + '"}'})]

    async def xread(
        self,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        assert streams == {"stream:events": "0"}
        assert count == 10
        assert block == 100
        return [
            (
                "stream:events",
                [("1-0", {"data": '{"content": "' + ("x" * 20_000) + '"}'})],
            )
        ]


def _disable_sentinel(monkeypatch) -> None:
    monkeypatch.setattr(redis_storage.settings, "REDIS_SENTINEL_HOSTS", "")
    monkeypatch.setattr(redis_storage.settings, "REDIS_SENTINEL_MASTER", "")
    monkeypatch.setattr(redis_storage.settings, "REDIS_SENTINEL_PASSWORD", None)
    redis_storage.get_redis_connection_pool.cache_clear()


def test_get_redis_client_returns_distinct_clients_sharing_one_pool(
    monkeypatch,
) -> None:
    _disable_sentinel(monkeypatch)
    pool = _FakePool()
    fake_module = _FakeAsyncRedisModule()
    fake_module.ConnectionPool.from_url = lambda *args, **kwargs: pool

    monkeypatch.setattr(redis_storage, "redis", fake_module)
    monkeypatch.setattr(redis_storage, "Redis", _FakeRedisClient)

    client_a = redis_storage.get_redis_client()
    client_b = redis_storage.get_redis_client()

    assert client_a is not client_b
    assert client_a.connection_pool is pool
    assert client_b.connection_pool is pool


def test_create_redis_client_can_use_an_isolated_pool(monkeypatch) -> None:
    _disable_sentinel(monkeypatch)
    pools: list[_FakePool] = []

    def _from_url(*args, **kwargs):
        pool = _FakePool()
        pools.append(pool)
        return pool

    fake_module = _FakeAsyncRedisModule()
    fake_module.ConnectionPool.from_url = _from_url

    monkeypatch.setattr(redis_storage, "redis", fake_module)
    monkeypatch.setattr(redis_storage, "Redis", _FakeRedisClient)
    redis_storage.get_redis_connection_pool.cache_clear()

    shared_client = redis_storage.create_redis_client()
    isolated_client = redis_storage.create_redis_client(isolated_pool=True)

    assert shared_client.connection_pool is redis_storage.get_redis_connection_pool()
    assert isolated_client.connection_pool is not shared_client.connection_pool
    assert isolated_client.connection_pool is pools[-1]


def test_create_redis_client_can_override_socket_timeout(monkeypatch) -> None:
    _disable_sentinel(monkeypatch)
    captured_kwargs: list[dict] = []

    def _from_url(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return _FakePool()

    fake_module = _FakeAsyncRedisModule()
    fake_module.ConnectionPool.from_url = _from_url

    monkeypatch.setattr(redis_storage, "redis", fake_module)
    monkeypatch.setattr(redis_storage, "Redis", _FakeRedisClient)
    redis_storage.get_redis_connection_pool.cache_clear()

    redis_storage.create_redis_client(isolated_pool=True, socket_timeout=None)

    assert captured_kwargs[-1]["socket_timeout"] is None


class _FakeSentinelMasterClient:
    def __init__(self, *, connection_pool) -> None:
        self.connection_pool = connection_pool
        self.auto_close_connection_pool = False


def _enable_sentinel(monkeypatch, **overrides) -> None:
    values = {
        "REDIS_URL": "redis://unused:6379/3",
        "REDIS_PASSWORD": None,
        "REDIS_SENTINEL_HOSTS": "s1:26379,s2:26379",
        "REDIS_SENTINEL_MASTER": "mymaster",
        "REDIS_SENTINEL_PASSWORD": None,
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(redis_storage.settings, name, value)
    redis_storage.get_redis_connection_pool.cache_clear()


def _install_fake_sentinel(monkeypatch) -> list[object]:
    constructed: list[object] = []

    class FakeSentinel:
        def __init__(self, sentinels, sentinel_kwargs=None, **kwargs) -> None:
            self.sentinels = sentinels
            self.sentinel_kwargs = sentinel_kwargs
            self.kwargs = kwargs
            constructed.append(self)

        def master_for(self, service_name, **kwargs):
            self.service_name = service_name
            pool = _FakePool()
            self.pool = pool
            return _FakeSentinelMasterClient(connection_pool=pool)

    class _SentinelForbiddenPoolFactory:
        @staticmethod
        def from_url(*args, **kwargs):
            raise AssertionError("ConnectionPool.from_url must not be used in sentinel mode")

    fake_module = _FakeAsyncRedisModule()
    fake_module.ConnectionPool = _SentinelForbiddenPoolFactory
    monkeypatch.setattr(redis_storage, "redis", fake_module)
    monkeypatch.setattr(redis_storage, "Sentinel", FakeSentinel)
    monkeypatch.setattr(redis_storage, "Redis", _FakeRedisClient)
    return constructed


def test_create_redis_client_uses_sentinel_when_configured(monkeypatch) -> None:
    _enable_sentinel(monkeypatch)
    constructed = _install_fake_sentinel(monkeypatch)

    client = redis_storage.create_redis_client()

    assert len(constructed) == 1
    sentinel = constructed[0]
    assert sentinel.sentinels == [("s1", 26379), ("s2", 26379)]
    assert sentinel.service_name == "mymaster"
    assert sentinel.kwargs["db"] == 3
    assert sentinel.kwargs["decode_responses"] is True
    assert sentinel.sentinel_kwargs is None
    assert client.connection_pool is sentinel.pool


def test_create_redis_client_isolated_pool_uses_sentinel_path(monkeypatch) -> None:
    _enable_sentinel(monkeypatch)
    constructed = _install_fake_sentinel(monkeypatch)

    shared_client = redis_storage.create_redis_client()
    isolated_client = redis_storage.create_redis_client(isolated_pool=True)

    assert len(constructed) == 2
    assert isolated_client.connection_pool is constructed[-1].pool
    assert isolated_client.connection_pool is not shared_client.connection_pool
    assert isolated_client.auto_close_connection_pool is True


def test_create_redis_client_sentinel_uses_node_and_sentinel_passwords(monkeypatch) -> None:
    _enable_sentinel(
        monkeypatch,
        REDIS_PASSWORD="node-secret",
        REDIS_SENTINEL_PASSWORD="sentinel-secret",
    )
    constructed = _install_fake_sentinel(monkeypatch)

    redis_storage.create_redis_client()

    sentinel = constructed[0]
    assert sentinel.kwargs["password"] == "node-secret"
    assert sentinel.sentinel_kwargs["password"] == "sentinel-secret"
    assert sentinel.sentinel_kwargs["socket_timeout"] == sentinel.kwargs["socket_timeout"]
    assert sentinel.sentinel_kwargs["socket_connect_timeout"] == sentinel.kwargs["socket_connect_timeout"]


def test_partial_sentinel_config_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(redis_storage.settings, "REDIS_SENTINEL_HOSTS", "s1:26379")
    monkeypatch.setattr(redis_storage.settings, "REDIS_SENTINEL_MASTER", "")
    redis_storage.get_redis_connection_pool.cache_clear()

    with pytest.raises(ValueError, match="partially configured"):
        redis_storage.create_redis_client()

    monkeypatch.setattr(redis_storage.settings, "REDIS_SENTINEL_HOSTS", "")
    monkeypatch.setattr(redis_storage.settings, "REDIS_SENTINEL_MASTER", "mymaster")

    with pytest.raises(ValueError, match="partially configured"):
        redis_storage.create_redis_client(isolated_pool=True)


@pytest.mark.asyncio
async def test_close_redis_client_closes_sentinel_shared_pool(monkeypatch) -> None:
    _enable_sentinel(monkeypatch)
    constructed = _install_fake_sentinel(monkeypatch)
    fake_logger = _FakeLogger()
    monkeypatch.setattr(redis_storage, "logger", fake_logger)

    await redis_storage.close_redis_client()

    assert constructed[0].pool.closed is True
    assert redis_storage.get_redis_connection_pool.cache_info().currsize == 0


def test_empty_sentinel_fields_keep_from_url(monkeypatch) -> None:
    _disable_sentinel(monkeypatch)
    constructed: list[object] = []

    class FakeSentinel:
        def __init__(self, *args, **kwargs) -> None:
            constructed.append((args, kwargs))
            raise AssertionError("Sentinel must not be used when sentinel fields are empty")

    class _StandalonePoolFactory:
        @staticmethod
        def from_url(*args, **kwargs):
            return _FakePool()

    fake_module = _FakeAsyncRedisModule()
    fake_module.ConnectionPool = _StandalonePoolFactory
    monkeypatch.setattr(redis_storage, "redis", fake_module)
    monkeypatch.setattr(redis_storage, "Redis", _FakeRedisClient)
    monkeypatch.setattr(redis_storage, "Sentinel", FakeSentinel)

    client = redis_storage.create_redis_client()

    assert constructed == []
    assert client.connection_pool is redis_storage.get_redis_connection_pool()


def test_create_redis_client_sentinel_uses_url_password_when_settings_password_missing(
    monkeypatch,
) -> None:
    _enable_sentinel(
        monkeypatch,
        REDIS_URL="redis://:url-password@unused:6379/3",
        REDIS_PASSWORD=None,
    )
    constructed = _install_fake_sentinel(monkeypatch)

    redis_storage.create_redis_client()

    assert constructed[0].kwargs["password"] == "url-password"


def test_parse_sentinel_hosts_rejects_empty_and_invalid_entries() -> None:
    with pytest.raises(ValueError, match="empty host"):
        redis_storage.parse_sentinel_hosts(
            SimpleNamespace(REDIS_SENTINEL_HOSTS="s1:26379,,s2:26379")
        )
    with pytest.raises(ValueError, match="expected host:port"):
        redis_storage.parse_sentinel_hosts(SimpleNamespace(REDIS_SENTINEL_HOSTS="noport"))
    with pytest.raises(ValueError, match="Invalid REDIS_SENTINEL_HOSTS port"):
        redis_storage.parse_sentinel_hosts(SimpleNamespace(REDIS_SENTINEL_HOSTS="s1:abc"))


@pytest.mark.asyncio
async def test_close_redis_client_closes_shared_connection_pool(monkeypatch) -> None:
    _disable_sentinel(monkeypatch)
    pool = _FakePool()
    fake_module = _FakeAsyncRedisModule()
    fake_module.ConnectionPool.from_url = lambda *args, **kwargs: pool
    fake_logger = _FakeLogger()

    monkeypatch.setattr(redis_storage, "redis", fake_module)
    monkeypatch.setattr(redis_storage, "Redis", _FakeRedisWithClosablePool)
    monkeypatch.setattr(redis_storage, "logger", fake_logger)
    redis_storage.get_redis_connection_pool.cache_clear()

    await redis_storage.close_redis_client()

    assert pool.closed is True
    assert redis_storage.get_redis_connection_pool.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_redis_storage_keys_uses_scan_instead_of_blocking_keys() -> None:
    storage = redis_storage.RedisStorage()
    storage._client = _FakeScanRedisClient()

    keys = await storage.keys("task:*")

    assert keys == ["task:a", "task:b", "task:c"]


@pytest.mark.asyncio
async def test_redis_storage_keys_limits_scan_results_by_default() -> None:
    storage = redis_storage.RedisStorage()
    client = _LargeFakeScanRedisClient()
    storage._client = client

    keys = await storage.keys("task:*")

    assert len(keys) == redis_storage.REDIS_STORAGE_KEYS_LIMIT
    assert keys[0] == "task:0"
    assert keys[-1] == f"task:{redis_storage.REDIS_STORAGE_KEYS_LIMIT - 1}"
    assert client.calls == redis_storage.REDIS_STORAGE_KEYS_LIMIT // 100


@pytest.mark.asyncio
async def test_redis_storage_set_offloads_json_serialization_for_structured_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = redis_storage.RedisStorage()
    client = _FakeSetRedisClient()
    storage._client = client
    calls = []

    async def _fake_run_blocking_io(func, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(redis_storage, "run_blocking_io", _fake_run_blocking_io, raising=False)

    await storage.set("cache:large", {"items": ["x" * 20_000]}, ttl=60)

    assert calls == [json.dumps]
    assert client.set_calls == [("cache:large", '{"items": ["' + ("x" * 20_000) + '"]}', 60)]


@pytest.mark.asyncio
async def test_redis_storage_get_offloads_json_parsing_for_structured_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = redis_storage.RedisStorage()
    storage._client = _FakeGetRedisClient()
    calls = []

    async def _fake_run_blocking_io(func, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(redis_storage, "run_blocking_io", _fake_run_blocking_io, raising=False)

    value = await storage.get("cache:large")

    assert value == {"items": ["x" * 20_000]}
    assert calls == [json.loads]


@pytest.mark.asyncio
async def test_redis_storage_xadd_offloads_json_serialization_for_dict_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = redis_storage.RedisStorage()
    client = _FakeXaddRedisClient()
    storage._client = client
    calls = []

    async def _fake_run_blocking_io(func, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(redis_storage, "run_blocking_io", _fake_run_blocking_io)

    entry_id = await storage.xadd(
        "stream:events",
        {"event_type": "message", "data": {"content": "x" * 20_000}},
        maxlen=100,
    )

    assert entry_id == "1-0"
    assert calls == [json.dumps]
    assert client.xadd_calls == [
        (
            "stream:events",
            {"event_type": "message", "data": '{"content": "' + ("x" * 20_000) + '"}'},
            100,
        )
    ]


@pytest.mark.asyncio
async def test_redis_storage_xrange_offloads_stream_field_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = redis_storage.RedisStorage()
    storage._client = _FakeStreamReadRedisClient()
    calls = []

    async def _fake_run_blocking_io(func, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(redis_storage, "run_blocking_io", _fake_run_blocking_io)

    entries = await storage.xrange("stream:events", count=10)

    assert calls == ["_parse_stream_entries_sync"]
    assert entries == [("1-0", {"data": {"content": "x" * 20_000}})]


@pytest.mark.asyncio
async def test_redis_storage_xread_offloads_stream_field_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = redis_storage.RedisStorage()
    storage._client = _FakeStreamReadRedisClient()
    calls = []

    async def _fake_run_blocking_io(func, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(redis_storage, "run_blocking_io", _fake_run_blocking_io)

    entries = await storage.xread({"stream:events": "0"}, count=10, block=100)

    assert calls == ["_parse_stream_read_result_sync"]
    assert entries == [("stream:events", [("1-0", {"data": {"content": "x" * 20_000}})])]
