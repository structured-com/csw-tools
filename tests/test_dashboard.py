import pytest

from csw_tools.dashboard import DashboardError, normalize_dashboard


@pytest.mark.parametrize(
    "value",
    [
        "my-company",
        "my-company.tetrationcloud.com",
        "https://my-company.tetrationcloud.com",
        "https://my-company.tetrationcloud.com/",
        "  HTTPS://MY-COMPANY.TETRATIONCLOUD.COM/  ",
    ],
)
def test_normalizes_supported_dashboard_forms(value: str) -> None:
    dashboard = normalize_dashboard(value)

    assert dashboard.name == "my-company"
    assert dashboard.fqdn == "my-company.tetrationcloud.com"
    assert dashboard.url == "https://my-company.tetrationcloud.com"


@pytest.mark.parametrize(
    ("value", "fqdn"),
    [
        ("csw.example.org", "csw.example.org"),
        ("https://csw.example.org", "csw.example.org"),
        ("https://csw.example.org/", "csw.example.org"),
        ("  CSW.EXAMPLE.ORG  ", "csw.example.org"),
        ("nested.my-company", "nested.my-company"),
    ],
)
def test_normalizes_non_saas_fqdn_and_https_origin(value: str, fqdn: str) -> None:
    dashboard = normalize_dashboard(value)

    assert dashboard.name == f"https://{fqdn}"
    assert dashboard.fqdn == fqdn
    assert dashboard.url == dashboard.name


def test_bare_and_explicit_non_saas_forms_share_identity() -> None:
    assert normalize_dashboard("csw.example.org") == normalize_dashboard(
        "https://csw.example.org"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "-company",
        "company-",
        "my_company",
        "my company",
        "a" * 64,
        "tetrationcloud.com",
        "bad_host.example.com",
        "192.0.2.1",
        "http://my-company.tetrationcloud.com",
        "https://[",
        "https://my-company.tetrationcloud.com:443",
        "https://user@my-company.tetrationcloud.com",
        "https://my-company.tetrationcloud.com/openapi",
        "https://my-company.tetrationcloud.com/?page=1",
        "https://my-company.tetrationcloud.com/?",
        "https://my-company.tetrationcloud.com/#section",
        "https://my-company.tetrationcloud.com/#",
    ],
)
def test_rejects_unsupported_dashboard_forms(value: str) -> None:
    with pytest.raises(DashboardError):
        normalize_dashboard(value)


@pytest.mark.parametrize(
    "url",
    [
        "https://csw.example.org",
        "https://192.0.2.1",
        "https://[2001:db8::1]",
        "https://csw-local",
    ],
)
def test_explicit_on_premises_origin_is_separate_from_saas(url: str) -> None:
    dashboard = normalize_dashboard(url)
    assert dashboard.url == url
    assert dashboard.name == url


@pytest.mark.parametrize(
    "url",
    [
        "https://bad_host.example.org",
        "https://csw.example.org:8443",
        "https://csw.example.org/api",
        "https://user:secret@csw.example.org",
    ],
)
def test_on_premises_preserves_url_safety_checks(url: str) -> None:
    with pytest.raises(DashboardError):
        normalize_dashboard(url)
