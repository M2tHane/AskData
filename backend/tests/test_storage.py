import hashlib
from io import BytesIO

import pytest

from app.core.errors import AppError, ErrorCode
from app.core.storage import MinioStore, object_key, validate_key


class FakeMinio:
    def __init__(self):
        self.objects = {}
        self.closed = False
        self.released = False

    def put_object(self, bucket, key, stream, length, content_type):
        self.objects[key] = stream.read(length)

    def get_object(self, bucket, key):
        store = self

        class Response(BytesIO):
            def close(self):
                store.closed = True
                super().close()

            def release_conn(self):
                store.released = True

        return Response(self.objects[key])

    def remove_object(self, bucket, key):
        self.objects.pop(key)


async def test_object_storage_roundtrip_and_close():
    client = FakeMinio()
    store = MinioStore(client, "test")
    key = object_key(1, 2, 3, "orders.csv")
    stored = await store.put(key, b"id\n1", "text/csv")
    assert stored.sha256 == hashlib.sha256(b"id\n1").hexdigest()
    assert await store.get(key) == b"id\n1"
    assert client.closed and client.released
    await store.delete(key)
    with pytest.raises(AppError) as caught:
        await store.get(key)
    assert caught.value.code == ErrorCode.STORAGE_UNAVAILABLE


@pytest.mark.parametrize("key", ["../x", "/x", "x/../y", "x\\y", "x//y", "x\ny", ""])
def test_invalid_storage_paths(key):
    with pytest.raises(AppError):
        validate_key(key)
