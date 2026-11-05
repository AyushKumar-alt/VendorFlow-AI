import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.requests import Request

from app.auth.context import OrganizationContext
from app.auth.dependencies import (
    get_identity_provider,
    reset_identity_provider,
    set_identity_provider,
)
from app.auth.provider import DevIdentityProvider, IdentityProvider
from app.core.config import Settings, settings
from app.db.session import get_db
from app.main import app
from app.models import Membership, Organization, User


@pytest.fixture(autouse=True)
def reset_provider_state_after_test():
    """Ensure custom identity provider state does not leak across tests."""
    yield
    reset_identity_provider()


@pytest.fixture
async def test_engine():
    """Create test engine targeting vendorflow_test.

    CRITICAL SAFETY RULE:
    Enforces that the database name is strictly 'vendorflow_test'.
    """
    test_url = settings.get_test_database_url()
    if test_url.database != "vendorflow_test":
        raise RuntimeError(
            f"Safety guard failure: Expected 'vendorflow_test', got '{test_url.database}'."
        )
    engine = create_async_engine(test_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(test_engine):
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest.fixture
async def client(db_session: AsyncSession):
    """Async HTTP client with get_db overridden to use isolated test db_session."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_test_data(db_session: AsyncSession):
    # Active organization A
    org_a = Organization(
        name="Auto Components Pune Pvt Ltd",
        slug=f"autocomp-{uuid.uuid4().hex[:8]}",
        status="active",
    )
    # Active organization B
    org_b = Organization(
        name="Bharat Precision Forgings",
        slug=f"bharat-forgings-{uuid.uuid4().hex[:8]}",
        status="active",
    )
    # Suspended organization C
    org_c = Organization(
        name="Suspended Industries Ltd",
        slug=f"suspended-org-{uuid.uuid4().hex[:8]}",
        status="suspended",
    )
    # Active users
    user_admin = User(
        email=f"admin_{uuid.uuid4().hex[:8]}@autocomp.com",
        hashed_password="hashed_admin_password",
        full_name="Rajesh Sharma",
        is_active=True,
    )
    user_reviewer = User(
        email=f"reviewer_{uuid.uuid4().hex[:8]}@autocomp.com",
        hashed_password="hashed_reviewer_password",
        full_name="Pooja Deshmukh",
        is_active=True,
    )
    user_inactive = User(
        email=f"inactive_{uuid.uuid4().hex[:8]}@autocomp.com",
        hashed_password="hashed_inactive_password",
        full_name="Vikram Joshi",
        is_active=False,
    )
    db_session.add_all([org_a, org_b, org_c, user_admin, user_reviewer, user_inactive])
    await db_session.flush()

    # Memberships in org_a
    mem_admin_a = Membership(
        organization_id=org_a.id,
        user_id=user_admin.id,
        role="admin",
        is_active=True,
    )
    mem_reviewer_a = Membership(
        organization_id=org_a.id,
        user_id=user_reviewer.id,
        role="reviewer",
        is_active=True,
    )
    # Inactive membership in org_c
    mem_inactive_in_c = Membership(
        organization_id=org_c.id,
        user_id=user_reviewer.id,
        role="reviewer",
        is_active=False,
    )
    # User reviewer also has an active membership in org_b as viewer
    mem_reviewer_b = Membership(
        organization_id=org_b.id,
        user_id=user_reviewer.id,
        role="viewer",
        is_active=True,
    )

    db_session.add_all([mem_admin_a, mem_reviewer_a, mem_inactive_in_c, mem_reviewer_b])
    await db_session.flush()

    return {
        "org_a": org_a,
        "org_b": org_b,
        "org_c": org_c,
        "user_admin": user_admin,
        "user_reviewer": user_reviewer,
        "user_inactive": user_inactive,
        "mem_admin_a": mem_admin_a,
        "mem_reviewer_a": mem_reviewer_a,
        "mem_inactive_in_c": mem_inactive_in_c,
        "mem_reviewer_b": mem_reviewer_b,
    }


async def test_valid_active_membership_resolves_successfully(
    client: httpx.AsyncClient, auth_test_data: dict
):
    user = auth_test_data["user_admin"]
    org = auth_test_data["org_a"]

    headers = {
        "X-Dev-User-Id": str(user.id),
        "X-Dev-Organization-Id": str(org.id),
    }
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == str(user.id)
    assert data["organization_id"] == str(org.id)
    assert data["role"] == "admin"
    assert data["user_email"] == user.email
    assert data["organization_slug"] == org.slug


async def test_organization_resolution_via_slug(
    client: httpx.AsyncClient, auth_test_data: dict
):
    user = auth_test_data["user_admin"]
    org = auth_test_data["org_a"]

    headers = {
        "X-Dev-User-Id": user.email,  # Test email identifier resolution
        "X-Organization-Slug": org.slug,  # Test slug resolution
    }
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == str(user.id)
    assert data["organization_id"] == str(org.id)
    assert data["role"] == "admin"


async def test_missing_identity_is_rejected(
    client: httpx.AsyncClient, auth_test_data: dict
):
    org = auth_test_data["org_a"]

    # No user identity header
    headers = {"X-Dev-Organization-Id": str(org.id)}
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 401
    assert "Missing authentication credentials" in response.json()["detail"]


async def test_nonexistent_user_is_rejected(
    client: httpx.AsyncClient, auth_test_data: dict
):
    org = auth_test_data["org_a"]
    nonexistent_user_id = str(uuid.uuid4())

    headers = {
        "X-Dev-User-Id": nonexistent_user_id,
        "X-Dev-Organization-Id": str(org.id),
    }
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 401
    assert "User not found" in response.json()["detail"]


async def test_missing_organization_header_is_rejected(
    client: httpx.AsyncClient, auth_test_data: dict
):
    user = auth_test_data["user_admin"]

    # User provided, but organization header omitted
    headers = {"X-Dev-User-Id": str(user.id)}
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 400
    assert "Missing organization context" in response.json()["detail"]


async def test_nonexistent_organization_is_rejected(
    client: httpx.AsyncClient, auth_test_data: dict
):
    user = auth_test_data["user_admin"]
    nonexistent_org_id = str(uuid.uuid4())

    headers = {
        "X-Dev-User-Id": str(user.id),
        "X-Dev-Organization-Id": nonexistent_org_id,
    }
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 404
    assert "Organization not found" in response.json()["detail"]


async def test_inactive_user_is_rejected(
    client: httpx.AsyncClient, auth_test_data: dict
):
    user_inactive = auth_test_data["user_inactive"]
    org = auth_test_data["org_a"]

    headers = {
        "X-Dev-User-Id": str(user_inactive.id),
        "X-Dev-Organization-Id": str(org.id),
    }
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 401
    assert "User account is inactive" in response.json()["detail"]


async def test_inactive_organization_is_rejected(
    client: httpx.AsyncClient, auth_test_data: dict
):
    user_reviewer = auth_test_data["user_reviewer"]
    org_suspended = auth_test_data["org_c"]

    headers = {
        "X-Dev-User-Id": str(user_reviewer.id),
        "X-Dev-Organization-Id": str(org_suspended.id),
    }
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 403
    assert "Organization is not active" in response.json()["detail"]


async def test_user_cannot_claim_another_organization_without_membership(
    client: httpx.AsyncClient, auth_test_data: dict
):
    # user_admin belongs to org_a, but NOT org_b
    user_admin = auth_test_data["user_admin"]
    org_b = auth_test_data["org_b"]

    headers = {
        "X-Dev-User-Id": str(user_admin.id),
        "X-Dev-Organization-Id": str(org_b.id),
    }
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 403
    assert (
        "User is not a member of the requested organization"
        in response.json()["detail"]
    )


async def test_inactive_membership_is_rejected(
    client: httpx.AsyncClient, auth_test_data: dict, db_session: AsyncSession
):
    # Deactivate reviewer's membership in org_a
    mem_reviewer = auth_test_data["mem_reviewer_a"]
    mem_reviewer.is_active = False
    await db_session.flush()

    user_reviewer = auth_test_data["user_reviewer"]
    org_a = auth_test_data["org_a"]

    headers = {
        "X-Dev-User-Id": str(user_reviewer.id),
        "X-Dev-Organization-Id": str(org_a.id),
    }
    response = await client.get("/api/v1/organization/context", headers=headers)

    assert response.status_code == 403
    assert (
        "User membership in the requested organization is inactive"
        in response.json()["detail"]
    )


async def test_role_authorization_enforcement(
    client: httpx.AsyncClient, auth_test_data: dict
):
    user_reviewer = auth_test_data["user_reviewer"]
    user_admin = auth_test_data["user_admin"]
    org_a = auth_test_data["org_a"]

    reviewer_headers = {
        "X-Dev-User-Id": str(user_reviewer.id),
        "X-Dev-Organization-Id": str(org_a.id),
    }
    admin_headers = {
        "X-Dev-User-Id": str(user_admin.id),
        "X-Dev-Organization-Id": str(org_a.id),
    }

    # 1. Reviewer can access reviewer-only endpoint
    resp_reviewer_ok = await client.get(
        "/api/v1/organization/reviewer-only", headers=reviewer_headers
    )
    assert resp_reviewer_ok.status_code == 200

    # 2. Reviewer is FORBIDDEN from admin-only endpoint
    resp_reviewer_admin = await client.get(
        "/api/v1/organization/admin-only", headers=reviewer_headers
    )
    assert resp_reviewer_admin.status_code == 403
    assert "Insufficient role permissions" in resp_reviewer_admin.json()["detail"]

    # 3. Admin can access admin-only endpoint
    resp_admin_ok = await client.get(
        "/api/v1/organization/admin-only", headers=admin_headers
    )
    assert resp_admin_ok.status_code == 200
    assert resp_admin_ok.json()["role"] == "admin"


async def test_replaceable_identity_provider(client: httpx.AsyncClient):
    """Verify that replacing the IdentityProvider via dependency override works seamlessly."""
    mock_user_id = uuid.uuid4()
    mock_org_id = uuid.uuid4()
    mock_mem_id = uuid.uuid4()

    class MockCustomOIDCProvider(IdentityProvider):
        async def resolve_context(
            self, request: httpx.Request, db: AsyncSession
        ) -> OrganizationContext:
            return OrganizationContext(
                user_id=mock_user_id,
                organization_id=mock_org_id,
                membership_id=mock_mem_id,
                role="owner",
                user_email="oidc_user@enterprise.com",
                user_full_name="Enterprise User",
                organization_name="Enterprise Org",
                organization_slug="enterprise-org",
            )

    mock_provider = MockCustomOIDCProvider()
    app.dependency_overrides[get_identity_provider] = lambda: mock_provider

    # Request without dev headers should still resolve via the overridden provider
    response = await client.get("/api/v1/organization/context")
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == str(mock_user_id)
    assert data["organization_id"] == str(mock_org_id)
    assert data["role"] == "owner"
    assert data["user_email"] == "oidc_user@enterprise.com"

    del app.dependency_overrides[get_identity_provider]


async def test_dev_identity_provider_operates_when_app_env_development(
    client: httpx.AsyncClient, auth_test_data: dict, monkeypatch: pytest.MonkeyPatch
):
    """Verify DevIdentityProvider operates normally when APP_ENV=development."""
    monkeypatch.setattr(settings, "app_env", "development")
    user = auth_test_data["user_admin"]
    org = auth_test_data["org_a"]

    headers = {
        "X-Dev-User-Id": str(user.id),
        "X-Dev-Organization-Id": str(org.id),
    }
    response = await client.get("/api/v1/organization/context", headers=headers)
    assert response.status_code == 200
    assert response.json()["user_id"] == str(user.id)


async def test_dev_identity_provider_rejects_in_production(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
):
    """Verify DevIdentityProvider directly rejects calls when APP_ENV=production."""
    monkeypatch.setattr(settings, "app_env", "production")
    provider = DevIdentityProvider()

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/organization/context",
        "headers": [(b"x-dev-user-id", b"some-user")],
    }
    starlette_req = Request(scope)

    with pytest.raises(HTTPException) as exc_info:
        await provider.resolve_context(starlette_req, db_session)

    assert exc_info.value.status_code == 403
    assert (
        "DevIdentityProvider is only permitted when APP_ENV=development"
        in exc_info.value.detail
    )
    assert "production" in exc_info.value.detail


async def test_dev_identity_provider_rejects_in_staging(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
):
    """Verify DevIdentityProvider rejects calls in non-development environments like staging."""
    monkeypatch.setattr(settings, "app_env", "staging")
    provider = DevIdentityProvider()

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/organization/context",
        "headers": [(b"x-dev-user-id", b"some-user")],
    }
    starlette_req = Request(scope)

    with pytest.raises(HTTPException) as exc_info:
        await provider.resolve_context(starlette_req, db_session)

    assert exc_info.value.status_code == 403
    assert (
        "DevIdentityProvider is only permitted when APP_ENV=development"
        in exc_info.value.detail
    )
    assert "staging" in exc_info.value.detail


def test_dev_identity_provider_not_silently_selected_in_production(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify get_identity_provider refuses to return DevIdentityProvider when APP_ENV=production."""
    monkeypatch.setattr(settings, "app_env", "production")
    reset_identity_provider()

    with pytest.raises(HTTPException) as exc_info:
        get_identity_provider()

    assert exc_info.value.status_code == 500
    assert "Production identity provider is not configured" in exc_info.value.detail
    assert "DevIdentityProvider is strictly disabled" in exc_info.value.detail


async def test_dev_identity_provider_cannot_be_activated_through_arbitrary_headers(
    client: httpx.AsyncClient,
    auth_test_data: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify arbitrary request headers cannot activate DevIdentityProvider in production."""
    monkeypatch.setattr(settings, "app_env", "production")
    reset_identity_provider()

    user = auth_test_data["user_admin"]
    org = auth_test_data["org_a"]

    # Attempt various header combinations to bypass or activate dev authentication
    malicious_headers = [
        {
            "X-Dev-User-Id": str(user.id),
            "X-Dev-Organization-Id": str(org.id),
            "X-Dev-Mode": "true",
        },
        {
            "X-Dev-User-Id": str(user.id),
            "X-Dev-Organization-Id": str(org.id),
            "X-Environment": "development",
        },
        {
            "X-Dev-User-Id": str(user.id),
            "X-Dev-Organization-Id": str(org.id),
            "X-Auth-Provider": "dev",
        },
        {
            "X-Dev-User-Id": str(user.id),
            "X-Dev-Organization-Id": str(org.id),
            "X-Bypass-Env-Check": "1",
        },
        {
            "X-Dev-User-Id": str(user.id),
            "X-Dev-Organization-Id": str(org.id),
        },
    ]

    for headers in malicious_headers:
        response = await client.get("/api/v1/organization/context", headers=headers)
        assert response.status_code == 500
        assert (
            "Production identity provider is not configured"
            in response.json()["detail"]
        )


async def test_production_environment_with_configured_provider_succeeds(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Verify that in production, when a legitimate production provider is registered, requests succeed."""
    monkeypatch.setattr(settings, "app_env", "production")
    mock_user_id = uuid.uuid4()
    mock_org_id = uuid.uuid4()
    mock_mem_id = uuid.uuid4()

    class MockProductionJWTProvider(IdentityProvider):
        async def resolve_context(
            self, request: httpx.Request, db: AsyncSession
        ) -> OrganizationContext:
            return OrganizationContext(
                user_id=mock_user_id,
                organization_id=mock_org_id,
                membership_id=mock_mem_id,
                role="owner",
                user_email="prod_user@enterprise.com",
                user_full_name="Production User",
                organization_name="Enterprise Corp",
                organization_slug="enterprise-corp",
            )

    set_identity_provider(MockProductionJWTProvider())
    try:
        response = await client.get("/api/v1/organization/context")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == str(mock_user_id)
        assert data["role"] == "owner"
        assert data["user_email"] == "prod_user@enterprise.com"
    finally:
        reset_identity_provider()


async def test_dev_identity_provider_rejected_even_if_injected_in_production(
    client: httpx.AsyncClient,
    auth_test_data: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify that even if DevIdentityProvider is injected via dependency override in production, it refuses to operate."""
    monkeypatch.setattr(settings, "app_env", "production")
    app.dependency_overrides[get_identity_provider] = lambda: DevIdentityProvider()

    user = auth_test_data["user_admin"]
    org = auth_test_data["org_a"]

    headers = {
        "X-Dev-User-Id": str(user.id),
        "X-Dev-Organization-Id": str(org.id),
    }
    try:
        response = await client.get("/api/v1/organization/context", headers=headers)
        assert response.status_code == 403
        assert (
            "DevIdentityProvider is only permitted when APP_ENV=development"
            in response.json()["detail"]
        )
    finally:
        del app.dependency_overrides[get_identity_provider]


def test_set_identity_provider_rejects_dev_provider_in_production(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify set_identity_provider refuses to register DevIdentityProvider in production."""
    monkeypatch.setattr(settings, "app_env", "production")

    with pytest.raises(ValueError) as exc_info:
        set_identity_provider(DevIdentityProvider())

    assert "Cannot register DevIdentityProvider" in str(exc_info.value)
    assert "strictly restricted to development environments" in str(exc_info.value)


def test_missing_app_env_defaults_to_production_fail_closed():
    """Verify that omitting APP_ENV defaults to production (fail-closed), never development."""
    from pydantic_settings import SettingsConfigDict

    class TestSettings(Settings):
        model_config = SettingsConfigDict(extra="ignore")

    # When no APP_ENV is in environment or env_file, it defaults to production
    test_settings = TestSettings(
        postgres_password="test_secret",
        _env_file=None,
    )
    assert test_settings.app_env == "production"
    assert test_settings.is_development is False
    assert test_settings.is_production is True


def test_invalid_app_env_raises_validation_error():
    """Verify that an invalid APP_ENV value raises a Pydantic ValidationError."""
    from pydantic import ValidationError
    from pydantic_settings import SettingsConfigDict

    class TestSettings(Settings):
        model_config = SettingsConfigDict(extra="ignore")

    with pytest.raises(ValidationError) as exc_info:
        TestSettings(
            app_env="invalid_environment",
            postgres_password="test_secret",
            _env_file=None,
        )

    errors = exc_info.value.errors()
    assert any(
        "Invalid APP_ENV" in str(err.get("msg", ""))
        or "invalid_environment" in str(err.get("msg", ""))
        for err in errors
    )


def test_empty_app_env_raises_validation_error():
    """Verify that an empty APP_ENV value raises a Pydantic ValidationError."""
    from pydantic import ValidationError
    from pydantic_settings import SettingsConfigDict

    class TestSettings(Settings):
        model_config = SettingsConfigDict(extra="ignore")

    with pytest.raises(ValidationError) as exc_info:
        TestSettings(
            app_env="   ",
            postgres_password="test_secret",
            _env_file=None,
        )

    errors = exc_info.value.errors()
    assert any("APP_ENV cannot be empty" in str(err.get("msg", "")) for err in errors)
