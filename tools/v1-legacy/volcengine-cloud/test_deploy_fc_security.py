from __future__ import annotations

import importlib.util
import sys
import types
import zipfile
from pathlib import Path

import pytest


CLOUD_DIR = Path(__file__).resolve().parent
if str(CLOUD_DIR) not in sys.path:
    sys.path.insert(0, str(CLOUD_DIR))

import deploy_fc


def _write_zip(path: Path, entries: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, data)


def _function(zip_path: Path) -> dict[str, object]:
    return {
        "name": "test-function",
        "zip_file": zip_path,
        "tos_key": "fc_packages/test-function.zip",
    }


def _load_tos_client(relative_path: str):
    path = CLOUD_DIR / relative_path
    module_name = "test_" + relative_path.replace("/", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_tos_module():
    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs

    return types.SimpleNamespace(
        TosClientV2=FakeClient,
        StaticCredentialsProvider=lambda ak, sk, token=None: ("static", ak, sk, token),
        EcsCredentialsProvider=lambda role_name: ("role", role_name),
    )


@pytest.fixture(autouse=True)
def _reset_results() -> None:
    deploy_fc.results.clear()


def test_rebuild_rejects_dotenv_before_mutating_archive_and_never_echoes_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = tmp_path / "unsafe-dotenv.zip"
    secret = "do-not-print-this-secret-value"
    _write_zip(
        archive,
        {
            "main.py": "def handler(event, context): return {}\n",
            ".env.volcengine": f"DDS_TOS_SECRET_ACCESS_KEY={secret}\n",
        },
    )
    original = archive.read_bytes()
    monkeypatch.setattr(deploy_fc, "FUNCTIONS", [_function(archive)])

    with pytest.raises(RuntimeError) as caught:
        deploy_fc.step0_rebuild_packages()

    captured = capsys.readouterr()
    disclosed = captured.out + captured.err + str(caught.value)
    assert secret not in disclosed
    assert archive.read_bytes() == original


@pytest.mark.parametrize(
    "entry_name, content",
    [
        (
            "config.txt",
            "-----BEGIN PRIVATE KEY-----\nprivate-material-must-not-print\n-----END PRIVATE KEY-----\n",
        ),
        (
            "settings.ini",
            "DDS_TOS_SECRET_ACCESS_KEY=high-confidence-secret-marker-12345\n",
        ),
    ],
)
def test_rebuild_rejects_high_confidence_secret_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_name: str,
    content: str,
) -> None:
    archive = tmp_path / "unsafe-content.zip"
    _write_zip(
        archive,
        {
            "index.py": "def handler(event, context): return {}\n",
            entry_name: content,
        },
    )
    monkeypatch.setattr(deploy_fc, "FUNCTIONS", [_function(archive)])

    with pytest.raises(RuntimeError):
        deploy_fc.step0_rebuild_packages()


def test_upload_rejects_credential_file_before_constructing_cloud_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "unsafe-credential-file.zip"
    _write_zip(
        archive,
        {
            "index.py": "def handler(event, context): return {}\n",
            "credentials.json": "{}\n",
        },
    )
    monkeypatch.setattr(deploy_fc, "FUNCTIONS", [_function(archive)])

    constructed: list[tuple[object, ...]] = []

    class FakeTosClient:
        def __init__(self, *args: object) -> None:
            constructed.append(args)

        def delete_object(self, *_args: object) -> None:
            raise AssertionError("unsafe package reached cloud delete")

        def put_object_from_file(self, *_args: object) -> None:
            raise AssertionError("unsafe package reached cloud upload")

    monkeypatch.setitem(sys.modules, "tos", types.SimpleNamespace(TosClientV2=FakeTosClient))

    with pytest.raises(RuntimeError):
        deploy_fc.step1_upload_packages(
            {
                "DDS_TOS_BUCKET": "test-bucket",
                "DDS_TOS_ENDPOINT": "tos.example.invalid",
                "DDS_TOS_REGION": "cn-test",
                "DDS_TOS_ACCESS_KEY_ID": "test-access-key",
                "DDS_TOS_SECRET_ACCESS_KEY": "test-secret-key",
            }
        )

    assert constructed == []


def test_clean_package_still_rebuilds_main_as_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "clean.zip"
    _write_zip(
        archive,
        {
            "main.py": "def handler(event, context): return {'ok': True}\n",
            "requirements.txt": "tos==2.9.2\n",
        },
    )
    monkeypatch.setattr(deploy_fc, "FUNCTIONS", [_function(archive)])

    deploy_fc.step0_rebuild_packages()

    with zipfile.ZipFile(archive) as rebuilt:
        assert "index.py" in rebuilt.namelist()
        assert "main.py" not in rebuilt.namelist()
        assert rebuilt.read("index.py").startswith(b"def handler")


@pytest.mark.parametrize(
    "relative_path",
    [
        "fc_governance.py",
        "fc_normalize.py",
        "fc_packages/governance/main.py",
        "fc_packages/normalize/main.py",
    ],
)
def test_fc_entrypoints_do_not_read_packaged_dotenv(relative_path: str) -> None:
    source = (CLOUD_DIR / relative_path).read_text(encoding="utf-8")

    assert ".env.volcengine" not in source
    assert "tos_client.load_dotenv(" not in source


@pytest.mark.parametrize(
    "relative_path",
    [
        "tos_client.py",
        "fc_packages/governance/tos_client.py",
        "fc_packages/normalize/tos_client.py",
    ],
)
def test_tos_client_uses_runtime_role_provider(
    relative_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_tos_client(relative_path)
    monkeypatch.setitem(sys.modules, "tos", _fake_tos_module())
    for key in (
        "DDS_TOS_ACCESS_KEY_ID",
        "DDS_TOS_SECRET_ACCESS_KEY",
        "DDS_TOS_SECURITY_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DDS_TOS_ENDPOINT", "tos.example.invalid")
    monkeypatch.setenv("DDS_TOS_REGION", "cn-test")
    monkeypatch.setenv("DDS_TOS_ROLE_NAME", "dds-fc-runtime-role")

    client = module.get_thread_local_client()

    assert client.args == ()
    assert client.kwargs["endpoint"] == "tos.example.invalid"
    assert client.kwargs["region"] == "cn-test"
    assert client.kwargs["credentials_provider"] == ("role", "dds-fc-runtime-role")


@pytest.mark.parametrize(
    "relative_path",
    [
        "tos_client.py",
        "fc_packages/governance/tos_client.py",
        "fc_packages/normalize/tos_client.py",
    ],
)
def test_tos_client_uses_runtime_static_credentials_with_security_token(
    relative_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_tos_client(relative_path)
    monkeypatch.setitem(sys.modules, "tos", _fake_tos_module())
    monkeypatch.delenv("DDS_TOS_ROLE_NAME", raising=False)
    monkeypatch.setenv("DDS_TOS_ENDPOINT", "tos.example.invalid")
    monkeypatch.setenv("DDS_TOS_REGION", "cn-test")
    monkeypatch.setenv("DDS_TOS_ACCESS_KEY_ID", "runtime-access-key")
    monkeypatch.setenv("DDS_TOS_SECRET_ACCESS_KEY", "runtime-secret-key")
    monkeypatch.setenv("DDS_TOS_SECURITY_TOKEN", "runtime-security-token")

    client = module.get_thread_local_client()

    assert client.args == ()
    assert client.kwargs["credentials_provider"] == (
        "static",
        "runtime-access-key",
        "runtime-secret-key",
        "runtime-security-token",
    )


def test_build_envs_prefers_runtime_role_over_long_lived_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEnv:
        def __init__(self, key: str, value: str) -> None:
            self.key = key
            self.value = value

    monkeypatch.setitem(
        sys.modules,
        "volcenginesdkvefaas",
        types.SimpleNamespace(EnvForCreateFunctionInput=FakeEnv),
    )

    envs = deploy_fc._build_envs(
        {
            "DDS_TOS_BUCKET": "test-bucket",
            "DDS_TOS_ENDPOINT": "tos.example.invalid",
            "DDS_TOS_REGION": "cn-test",
            "DDS_TOS_ROLE_NAME": "dds-fc-runtime-role",
            "DDS_TOS_ACCESS_KEY_ID": "control-plane-access-key",
            "DDS_TOS_SECRET_ACCESS_KEY": "control-plane-secret-key",
        }
    )
    by_key = {item.key: item.value for item in envs}

    assert by_key["DDS_TOS_ROLE_NAME"] == "dds-fc-runtime-role"
    assert "DDS_TOS_ACCESS_KEY_ID" not in by_key
    assert "DDS_TOS_SECRET_ACCESS_KEY" not in by_key
