from app import config


def test_defaults_present():
    assert config.S3_BUCKET
    assert config.DATABASE_URL.startswith("postgresql")
    assert config.S3_ENDPOINT.startswith("http")


def test_env_override(monkeypatch):
    import importlib
    monkeypatch.setenv("S3_BUCKET", "custom-bucket")
    importlib.reload(config)
    assert config.S3_BUCKET == "custom-bucket"
