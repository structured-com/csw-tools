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
    "value",
    [
        "",
        "   ",
        "-company",
        "company-",
        "my_company",
        "my company",
        "a" * 64,
        "nested.my-company",
        "tetrationcloud.com",
        "my-company.example.com",
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
