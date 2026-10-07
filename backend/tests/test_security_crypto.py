import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.crypto import SecretBox
from app.core.errors import AppError
from app.core.security import RefreshTokens, TokenService, hash_password, verify_password
from tests.fakes import MemoryRefreshStore


def test_passwords_and_invalid_hashes():
    password = SecretStr("correct horse battery staple")
    hashed = hash_password(password)
    assert password.get_secret_value() not in hashed
    assert verify_password(password, hashed)
    assert not verify_password(SecretStr("wrong"), hashed)
    assert not verify_password(password, "invalid-hash")


def test_access_tokens_signature_expiry_and_kind():
    tokens = TokenService(SecretStr("x" * 32))
    token = tokens.issue_access(3)
    assert tokens.verify_access(token).sub == "3"
    with pytest.raises(AppError):
        TokenService(SecretStr("y" * 32)).verify_access(token)
    with pytest.raises(AppError):
        tokens.verify_access(TokenService(SecretStr("x" * 32), minutes=-1).issue_access(3))
    with pytest.raises(AppError):
        tokens.verify_access("invalid")
    with pytest.raises(ValueError):
        TokenService(SecretStr("short"))


async def test_refresh_rotation_replay_and_expiry():
    store = MemoryRefreshStore()
    tokens = RefreshTokens(store)
    token = await tokens.issue(3)
    assert token.get_secret_value() not in str(store.records)
    user, replacement = await tokens.rotate(token)
    assert user == 3 and replacement != token
    with pytest.raises(AppError):
        await tokens.rotate(token)
    expired = await RefreshTokens(store, days=-1).issue(3)
    with pytest.raises(AppError):
        await tokens.rotate(expired)


def test_secret_box_roundtrip_tampering_and_masks():
    box = SecretBox(SecretStr(Fernet.generate_key().decode()))
    value = SecretStr("api-key-keep-private")
    encrypted = box.encrypt(value)
    assert value.get_secret_value() not in encrypted
    assert box.decrypt(encrypted) == value
    assert box.mask(value) == "********"
    assert box.mask() == ""
    with pytest.raises(AppError):
        box.decrypt(encrypted[:-1] + "!")
