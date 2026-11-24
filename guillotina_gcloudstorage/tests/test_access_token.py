from guillotina.response import HTTPNotFound
from guillotina.response import HTTPServiceUnavailable
from guillotina_gcloudstorage import storage
from guillotina_gcloudstorage.storage import GCloudFileManager
from guillotina_gcloudstorage.storage import GoogleCloudException
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

import asyncio
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def clean_token_cache():
    storage.invalidate_access_token()
    storage._metadata_token.pending = None
    yield
    storage.invalidate_access_token()
    storage._metadata_token.pending = None


async def test_concurrent_callers_share_one_token_fetch():
    release = asyncio.Event()

    async def slow_fetch():
        await release.wait()
        return "token-1", 3600

    fetch = AsyncMock(side_effect=slow_fetch)
    with patch.object(storage, "_fetch_metadata_token", fetch):
        callers = [asyncio.ensure_future(storage.get_access_token()) for _ in range(5)]
        await asyncio.sleep(0)
        release.set()
        assert await asyncio.gather(*callers) == ["token-1"] * 5
    assert fetch.await_count == 1


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


async def test_metadata_token_is_reused_until_refresh_margin():
    clock = FakeClock()
    fetch = AsyncMock(side_effect=[("token-1", 3600), ("token-2", 3600)])
    with patch.object(storage, "_fetch_metadata_token", fetch), patch.object(
        storage.time, "time", clock
    ):
        assert await storage.get_access_token() == "token-1"
        clock.now += 3600 - storage.TOKEN_REFRESH_MARGIN - 1
        assert await storage.get_access_token() == "token-1"
        clock.now += 2
        assert await storage.get_access_token() == "token-2"
    assert fetch.await_count == 2


async def test_short_lived_token_is_refreshed_before_it_expires():
    clock = FakeClock()
    fetch = AsyncMock(side_effect=[("old", 120), ("new", 3600)])
    with patch.object(storage, "_fetch_metadata_token", fetch), patch.object(
        storage.time, "time", clock
    ):
        assert await storage.get_access_token() == "old"
        clock.now += 59
        assert await storage.get_access_token() == "old"
        clock.now += 2
        assert await storage.get_access_token() == "new"


async def test_invalidate_only_drops_the_rejected_token():
    fetch = AsyncMock(side_effect=[("token-1", 3600), ("token-2", 3600)])
    with patch.object(storage, "_fetch_metadata_token", fetch):
        assert await storage.get_access_token() == "token-1"
        storage.invalidate_access_token("some-other-token")
        assert await storage.get_access_token() == "token-1"
        storage.invalidate_access_token("token-1")
        assert await storage.get_access_token() == "token-2"


async def test_failed_token_fetch_is_not_cached():
    fetch = AsyncMock(
        side_effect=[GoogleCloudException("500: unavailable"), ("token-1", 3600)]
    )
    with patch.object(storage, "_fetch_metadata_token", fetch):
        with pytest.raises(GoogleCloudException):
            await storage.get_access_token()
        assert await storage.get_access_token() == "token-1"


class FakeResponse:
    def __init__(self, status, body=b""):
        self.status = status
        self._body = body
        self.content = self

    async def text(self):
        return self._body.decode()

    async def read(self, _size):
        body, self._body = self._body, b""
        return body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.auth_headers = []

    def get(self, url, headers=None, **kwargs):
        self.auth_headers.append(headers.get("AUTHORIZATION"))
        return self._responses.pop(0)


class FakeBlobStore:
    def __init__(self, session, tokens):
        self.session = session
        self._tokens = list(tokens)
        self.invalidated = []

    async def get_access_token(self):
        return self._tokens[0]

    def invalidate_access_token(self, token=None):
        self.invalidated.append(token)
        if len(self._tokens) > 1:
            self._tokens.pop(0)

    async def get_bucket_name(self):
        return "bucket"


def _manager(util):
    manager = GCloudFileManager(None, None, None)
    return manager, patch.object(storage, "get_utility", return_value=util)


async def test_download_retries_once_with_a_fresh_token_after_401():
    session = FakeSession([FakeResponse(401, b"expired"), FakeResponse(200, b"data")])
    util = FakeBlobStore(session, ["expired-token", "fresh-token"])
    manager, patched = _manager(util)
    with patched:
        chunks = [c async for c in manager.iter_data(uri="account/file")]
    assert chunks == [b"data"]
    assert util.invalidated == ["expired-token"]
    assert session.auth_headers == ["Bearer expired-token", "Bearer fresh-token"]


async def test_download_rejected_twice_is_not_reported_as_not_found():
    session = FakeSession([FakeResponse(401, b"no"), FakeResponse(401, b"no")])
    util = FakeBlobStore(session, ["token-1", "token-2"])
    manager, patched = _manager(util)
    with patched:
        with pytest.raises(HTTPServiceUnavailable):
            async for _ in manager.iter_data(uri="account/file"):
                pass


async def test_download_of_missing_object_is_still_not_found():
    session = FakeSession([FakeResponse(404, b"missing")])
    util = FakeBlobStore(session, ["token-1"])
    manager, patched = _manager(util)
    with patched:
        with pytest.raises(HTTPNotFound):
            async for _ in manager.iter_data(uri="account/file"):
                pass
    assert util.invalidated == []


async def test_exists_retries_with_a_fresh_token_after_401():
    session = FakeSession([FakeResponse(401), FakeResponse(200)])
    util = FakeBlobStore(session, ["expired-token", "fresh-token"])
    manager, patched = _manager(util)
    file = SimpleNamespace(uri="account/file")
    with patched, patch.object(
        storage, "_is_uploaded_file", return_value=True
    ), patch.object(manager, "field", create=True) as field:
        field.get.return_value = file
        field.context = None
        assert await manager.exists() is True
    assert util.invalidated == ["expired-token"]
