"""`storage/db.connect()` in password mode (feature 001, unchanged) and Entra mode (002 FR-017a,
FR-020; contracts/cli-contract.md, Environment; data-model.md §1).

No connection is opened: `psycopg.connect` and `ManagedIdentityCredential` are replaced by fakes,
and the suite-wide network guard stays active.
"""

from typing import Any, ClassVar

import psycopg
import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError

from market_anomaly_agent.storage import db

PASSWORD_URL = "postgresql://market_anomaly_agent:SECRET-db-pw@db.example:5432/maa"
ENTRA_URL = (
    "postgresql://id-maa-job@psql-maa-test.postgres.database.azure.com:5432/market_anomaly_agent"
    "?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt"
)
CLIENT_ID = "client-id-placeholder"
SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


class FakePsycopg:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def connect(self, *args: Any, **kwargs: Any) -> object:
        self.calls.append((args, kwargs))
        return object()


class FakeCredential:
    """Stands in for azure.identity.ManagedIdentityCredential."""

    instances: ClassVar[list["FakeCredential"]] = []
    tokens: ClassVar[list[str]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, *, client_id: str | None = None) -> None:
        self.client_id = client_id
        self.scopes: list[tuple[str, ...]] = []
        FakeCredential.instances.append(self)

    def get_token(self, *scopes: str) -> AccessToken:
        self.scopes.append(scopes)
        if FakeCredential.error is not None:
            raise FakeCredential.error
        return AccessToken(FakeCredential.tokens.pop(0), 4_102_444_800)


@pytest.fixture
def fake_psycopg(monkeypatch: pytest.MonkeyPatch) -> FakePsycopg:
    fake = FakePsycopg()
    monkeypatch.setattr(psycopg, "connect", fake.connect)
    return fake


@pytest.fixture
def fake_credential(monkeypatch: pytest.MonkeyPatch) -> type[FakeCredential]:
    FakeCredential.instances = []
    FakeCredential.tokens = ["entra-token-0001", "entra-token-0002"]
    FakeCredential.error = None
    monkeypatch.setattr(db, "ManagedIdentityCredential", FakeCredential)
    return FakeCredential


@pytest.fixture
def entra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_AUTH", "entra")
    monkeypatch.setenv("DATABASE_URL", ENTRA_URL)
    monkeypatch.setenv("AZURE_CLIENT_ID", CLIENT_ID)


def test_entra_constants() -> None:
    assert db.AUTH_ENV_VAR == "DATABASE_AUTH"
    assert db.CLIENT_ID_ENV_VAR == "AZURE_CLIENT_ID"
    assert db.ENTRA_SCOPE == SCOPE


# (a) password mode: feature 001 behaviour, unchanged


@pytest.mark.parametrize(
    "auth",
    [
        pytest.param(None, id="unset"),
        pytest.param("", id="empty"),  # a blank `DATABASE_AUTH=` line in .env
        pytest.param("password", id="password"),
    ],
)
def test_password_mode_connects_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch, fake_psycopg: FakePsycopg, auth: str | None
) -> None:
    monkeypatch.setenv("DATABASE_URL", PASSWORD_URL)
    if auth is None:
        monkeypatch.delenv("DATABASE_AUTH", raising=False)
    else:
        monkeypatch.setenv("DATABASE_AUTH", auth)

    db.connect()

    assert fake_psycopg.calls == [((PASSWORD_URL,), {"autocommit": True})]


def test_missing_database_url_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_AUTH", raising=False)
    with pytest.raises(db.DatabaseConfigError, match="DATABASE_URL"):
        db.connect()


# (b) Entra mode: managed-identity token as the connection password


@pytest.mark.usefixtures("entra_env")
def test_entra_mode_uses_a_managed_identity_token_as_the_password(
    fake_psycopg: FakePsycopg, fake_credential: type[FakeCredential]
) -> None:
    db.connect()

    assert [c.client_id for c in fake_credential.instances] == [CLIENT_ID]
    assert fake_credential.instances[0].scopes == [(SCOPE,)]
    assert fake_psycopg.calls == [
        ((ENTRA_URL,), {"password": "entra-token-0001", "autocommit": True})
    ]
    assert "entra-token-0001" in db.issued_tokens()


@pytest.mark.usefixtures("entra_env")
def test_entra_mode_fetches_a_token_for_every_connection(
    fake_psycopg: FakePsycopg, fake_credential: type[FakeCredential]
) -> None:
    db.connect()
    db.connect()

    passwords = [kwargs["password"] for _, kwargs in fake_psycopg.calls]
    assert passwords == ["entra-token-0001", "entra-token-0002"]
    assert sum(len(c.scopes) for c in fake_credential.instances) == 2
    assert {"entra-token-0001", "entra-token-0002"} <= set(db.issued_tokens())


# (c) Entra mode: configuration errors, raised before any token request


@pytest.mark.usefixtures("entra_env")
@pytest.mark.parametrize(
    ("variable", "value"),
    [
        pytest.param("AZURE_CLIENT_ID", None, id="client-id-missing"),
        pytest.param(
            "DATABASE_URL",
            "postgresql://id-maa-job:SECRET-pw@psql-maa-test.postgres.database.azure.com:5432/"
            "market_anomaly_agent?sslmode=verify-full",
            id="url-with-password",
        ),
        pytest.param(
            "DATABASE_URL",
            "postgresql://id-maa-job@psql-maa-test.postgres.database.azure.com:5432/"
            "market_anomaly_agent?sslmode=require",
            id="sslmode-not-verify-full",
        ),
        pytest.param(
            "DATABASE_URL",
            "postgresql://id-maa-job@psql-maa-test.postgres.database.azure.com:5432/"
            "market_anomaly_agent",
            id="sslmode-missing",
        ),
    ],
)
def test_entra_mode_config_errors_raise_before_a_token_is_requested(
    monkeypatch: pytest.MonkeyPatch,
    fake_psycopg: FakePsycopg,
    fake_credential: type[FakeCredential],
    variable: str,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, value)

    with pytest.raises(db.DatabaseConfigError) as excinfo:
        db.connect()

    assert all(c.scopes == [] for c in fake_credential.instances)
    assert fake_psycopg.calls == []
    assert "SECRET-pw" not in str(excinfo.value)


# (d) unknown DATABASE_AUTH value


@pytest.mark.parametrize("auth", ["Entra", "token", "managed-identity"])
def test_unknown_auth_mode_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, fake_psycopg: FakePsycopg, auth: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", PASSWORD_URL)
    monkeypatch.setenv("DATABASE_AUTH", auth)

    with pytest.raises(db.DatabaseConfigError, match="DATABASE_AUTH"):
        db.connect()

    assert fake_psycopg.calls == []


# (e) a token failure propagates; the CLI turns it into exit 1


@pytest.mark.usefixtures("entra_env")
def test_entra_token_failure_propagates(
    fake_psycopg: FakePsycopg, fake_credential: type[FakeCredential]
) -> None:
    fake_credential.error = ClientAuthenticationError("managed identity unavailable")

    with pytest.raises(ClientAuthenticationError):
        db.connect()

    assert fake_psycopg.calls == []
