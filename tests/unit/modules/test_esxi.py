"""
    :codeauthor: VMware
"""
import logging
import os
import uuid
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
import saltext.vmware.modules.esxi as esxi
import saltext.vmware.utils.connect
import saltext.vmware.utils.esxi as esxi_utils
from config_modules_vmware.schema.schema_utility import Product
from config_modules_vmware.schema.schema_utility import retrieve_reference_schema
from pytest import MonkeyPatch
from salt.exceptions import SaltException

log = logging.getLogger(__name__)


@pytest.fixture
def tgz_file(session_temp_dir):
    tgz = session_temp_dir / "vmware.tgz"
    tgz.write_bytes(b"1")
    yield tgz


@pytest.fixture
def configure_loader_modules():
    return {esxi: {"__opts__": {}, "__pillar__": {}}}


@pytest.fixture(autouse=True)
def patch_salt_loaded_objects():
    # This esxi needs to be the same as the module we're importing
    with patch(
        "saltext.vmware.modules.esxi.__opts__",
        {
            "cachedir": ".",
            "saltext.vmware": {"host": "fnord.example.com", "user": "fnord", "password": "fnord"},
        },
        create=True,
    ), patch.object(esxi, "__pillar__", {}, create=True), patch.object(
        esxi, "__salt__", {}, create=True
    ):
        yield


@pytest.fixture
def fake_hosts():
    hosts = [MagicMock()]
    hosts[0].name = "blerp"
    hosts[
        0
    ].configManager.firmwareSystem.QueryFirmwareConfigUploadURL.return_value = "something/cool/*"

    with patch("saltext.vmware.utils.esxi.get_hosts", autospec=True, return_value=hosts):
        yield hosts


@pytest.fixture
def fake_http_query():
    fake_query = MagicMock()
    with patch.dict(esxi.__salt__, {"http.query": fake_query}):
        yield fake_query


@pytest.fixture
def dummy_cluster_paths():
    return ["path/to/cluster"]


@pytest.fixture
def dummy_configs():
    return ["config.module.submodule"]


@pytest.fixture
def fake_esx_config():
    fake = MagicMock()
    fake.get_configuration.return_value = {
        "path/to/cluster": {"config.module.submodule": "current"}
    }
    fake.get_desired_configuration.return_value = {
        "path/to/cluster": {"config.module.submodule": "desired"}
    }
    return fake


# Define some example test data
profile = "example_profile"
cluster_paths = ["cluster1", "cluster2"]
desired_state_spec = {"key": "value"}


@pytest.fixture
def create_esx_config_mock():
    with patch("saltext.vmware.utils.esxi.create_esx_config") as create_esx_config_mock:
        yield create_esx_config_mock


@pytest.fixture
def pre_check_deps(esx_config_mock, create_esx_config_mock):
    create_esx_config_mock.return_value = esx_config_mock
    return esx_config_mock


@pytest.fixture
def remediate_deps(esx_config_mock, create_esx_config_mock):
    create_esx_config_mock.return_value = esx_config_mock
    return esx_config_mock


def get_host(in_maintenance_mode=None):
    host = MagicMock()
    host.name = uuid.uuid4().hex
    host.RebootHost_Task.return_value = MagicMock()
    host.PowerDownHostToStandBy_Task.return_value = MagicMock()
    host.PowerUpHostFromStandBy_Task.return_value = MagicMock()
    host.ShutdownHost_Task.return_value = MagicMock()
    host.ShutdownHost_Task.return_value = MagicMock()
    host.configManager.firmwareSystem.BackupFirmwareConfiguration = MagicMock(
        return_value="http://vmware.tgz"
    )
    host.configManager.firmwareSystem.QueryFirmwareConfigUploadURL = MagicMock(
        return_value="http://vmware.tgz"
    )
    host.runtime.inMaintenanceMode = in_maintenance_mode
    host.EnterMaintenanceMode_Task.return_value = MagicMock()
    host.configManager.firmwareSystem.RestoreFirmwareConfiguration.return_value = MagicMock()
    host.configManager.firmwareSystem.ResetFirmwareToFactoryDefaults.return_value = MagicMock()
    host.ExitMaintenanceMode_Task.return_value = MagicMock()
    return host


@pytest.mark.parametrize(
    ["hosts", "fn_calls", "expected"],
    [
        [[], 0, None],
        [[get_host()], 1, True],
        [[get_host(), get_host()], 2, True],
    ],
)
@pytest.mark.parametrize(
    ["state", "fn"],
    [
        ["reboot", "RebootHost_Task"],
        ["standby", "PowerDownHostToStandBy_Task"],
        ["poweron", "PowerUpHostFromStandBy_Task"],
        ["shutdown", "ShutdownHost_Task"],
    ],
)
def test_esxi_power_state(hosts, state, fn, fn_calls, expected, fake_service_instance):
    _, service_instance = fake_service_instance

    patch_get_hosts = patch(
        "saltext.vmware.utils.esxi.get_hosts", autospec=True, return_value=hosts
    )
    patch_wait_for_task = patch(
        "saltext.vmware.utils.common.wait_for_task", autospec=True, return_value=None
    )

    with patch_get_hosts, patch_wait_for_task:
        ret = esxi.power_state(state=state, service_instance=service_instance)

    cnt = 0
    for h in hosts:
        mock_func = getattr(h, fn)
        cnt += mock_func.call_count
        # the get_host() fixtures are shared between other test runs
        mock_func.reset_mock()
    assert cnt == fn_calls
    assert ret is expected


@pytest.mark.parametrize(
    ["hosts", "push_file_to_master"],
    [
        [[get_host(), get_host()], False],
        [[get_host(), get_host()], True],
    ],
)
def test_esxi_backup_config(hosts, push_file_to_master, session_temp_dir, fake_service_instance):
    _, service_instance = fake_service_instance
    patch_get_hosts = patch(
        "saltext.vmware.utils.esxi.get_hosts", autospec=True, return_value=hosts
    )
    patch_wait_for_task = patch(
        "saltext.vmware.utils.common.wait_for_task", autospec=True, return_value=None
    )
    fake_http_query = MagicMock(return_value={"body": b"1"})
    patch_salt = patch.dict(
        esxi.__salt__,
        {
            "http.query": fake_http_query,
            "cp.push": MagicMock(return_value=True),
            "cp.cache_file": MagicMock(return_value=str(tgz_file)),
        },
        update=True,
    )
    patch_opts = patch.dict(esxi.__opts__, {"cachedir": str(session_temp_dir)})
    with patch_get_hosts, patch_wait_for_task, patch_salt, patch_opts:
        ret = esxi.backup_config(
            push_file_to_master=push_file_to_master, service_instance=service_instance
        )
        assert ret
        for host in hosts:
            assert ret[host.name]["file_name"] == str(session_temp_dir / "vmware.tgz")
            assert ret[host.name]["url"] == "http://vmware.tgz"
        assert push_file_to_master == (esxi.__salt__["cp.push"].call_count > 0)


@pytest.mark.parametrize(
    ["hosts", "source_file"],
    [
        [[get_host(), get_host()], None],
        [[get_host(), get_host()], "http://vmware.tgz"],
        [[get_host(), get_host()], "salt://vmware.tgz"],
    ],
)
def test_esxi_restore_config(hosts, source_file, tgz_file):
    if source_file is None:
        source_file = str(tgz_file)
    esxi.__opts__["saltext.vmware"]["esxi_host"] = esxi.__opts__["saltext.vmware"].get(
        "esxi_host", {}
    )
    for host in hosts:
        esxi.__opts__["saltext.vmware"]["esxi_host"][host.name] = {
            "user": esxi.__opts__["saltext.vmware"]["user"],
            "password": esxi.__opts__["saltext.vmware"]["password"],
        }
    fake_http_query = MagicMock(return_value={"body": b"1"})
    patch_salt = patch.dict(
        esxi.__salt__,
        {
            "http.query": fake_http_query,
            "cp.cache_file": MagicMock(return_value=str(tgz_file)),
        },
        update=True,
    )
    patch_get_hosts = patch(
        "saltext.vmware.utils.esxi.get_hosts", autospec=True, return_value=hosts
    )
    patch_wait_for_task = patch("saltext.vmware.utils.common.wait_for_task", autospec=True)
    with patch_get_hosts, patch_wait_for_task, patch_salt:
        ret = esxi.restore_config(source_file=source_file, service_instance=MagicMock())
    assert ret
    for host in hosts:
        assert ret[host.name] is True


@pytest.mark.parametrize(
    "expected_kwargs, host_name",
    [
        ({"host_names": None, "get_all_hosts": True}, None),
        ({"host_names": ["roscivs.example.com"], "get_all_hosts": False}, "roscivs.example.com"),
    ],
)
def test_esxi_restore_config_should_request_correct_hosts(expected_kwargs, host_name):
    # host_names should be a list or None, and get_all_hosts should be True or
    # False depending on if a host_name was provided
    fake_si = MagicMock()
    with patch(
        "saltext.vmware.utils.esxi.get_hosts", autospec=True, return_value=[]
    ) as fake_get_hosts:
        esxi.restore_config(host_name=host_name, source_file=None, service_instance=fake_si)
    fake_get_hosts.assert_called_with(
        service_instance=fake_si,
        cluster_name=None,
        datacenter_name=None,
        **expected_kwargs,
    )


def test_esxi_restore_config_should_send_correct_data_to_config_api_endpoint(
    fake_hosts, session_temp_dir, fake_http_query
):
    expected_url = "something/cool/blerp"
    expected_username = "roscivs"
    expected_password = "bottia"
    expected_data = b"hello world!"
    opts = {
        "saltext.vmware": {
            "user": "wrong username",
            "password": "wrong password",
            "esxi_host": {
                fake_hosts[0].name: {"user": expected_username, "password": expected_password},
            },
        },
    }
    sfile = session_temp_dir / "fnord"
    sfile.write_bytes(expected_data)
    with patch.dict(esxi.__opts__, opts):
        esxi.restore_config(source_file=str(sfile), service_instance=MagicMock())
    fake_http_query.assert_called_with(
        expected_url,
        data=expected_data,
        method="PUT",
        username=expected_username,
        password=expected_password,
    )


@pytest.mark.parametrize(
    ["hosts"],
    [
        [[get_host(), get_host()]],
    ],
)
def test_esxi_reset_config(hosts, fake_service_instance):
    _, service_instance = fake_service_instance
    patch_get_hosts = patch(
        "saltext.vmware.utils.esxi.get_hosts", autospec=True, return_value=hosts
    )
    patch_wait_for_task = patch(
        "saltext.vmware.utils.common.wait_for_task", autospec=True, return_value=None
    )
    patch_opts = patch.dict(esxi.__opts__, {"cachedir": "."})
    fake_http_query = MagicMock(return_value={"body": b"1"})
    patch_salt = patch.dict(
        esxi.__salt__,
        {
            "http.query": fake_http_query,
            "cp.cache_file": MagicMock(return_value="vmware.tgz"),
        },
        update=True,
    )
    with patch_get_hosts, patch_wait_for_task, patch_salt, patch_opts:
        ret = esxi.reset_config(service_instance=service_instance)
        assert ret
        for host in hosts:
            assert ret[host.name]


@pytest.mark.parametrize(
    ["hosts"],
    [
        [[get_host(), get_host()]],
    ],
)
def test_ntp_config(hosts, fake_service_instance):
    _, service_instance = fake_service_instance

    patch_get_hosts = patch(
        "saltext.vmware.utils.esxi.get_hosts", autospec=True, return_value=hosts
    )
    with patch_get_hosts:
        ret = esxi.get_ntp_config(service_instance=service_instance)
        assert ret
        expected = {
            "ntp_config_file",
            "time_zone",
            "time_zone_description",
            "time_zone_name",
            "ntp_servers",
            "time_zone_gmt_offset",
        }
        for host in ret:
            assert not expected - set(ret[host])

        ret = esxi.set_ntp_config(
            ntp_servers=["192.174.1.100", "192.174.1.200"], service_instance=service_instance
        )
        assert ret


def test_get_reference_schema():
    """
    Test to retrieve reference schema
    """
    reference_schema = esxi.retrieve_reference_schema(Product.ESX)
    assert reference_schema is not None


def test_get_configuration_all(fake_esx_config):
    configuration = esxi.get_configuration(esx_config=fake_esx_config)
    assert configuration == {"path/to/cluster": {"config.module.submodule": "current"}}


def test_get_desired_configuration_all(fake_esx_config):
    configuration = esxi.get_desired_configuration(esx_config=fake_esx_config)
    assert configuration == {"path/to/cluster": {"config.module.submodule": "desired"}}


@patch("saltext.vmware.modules.esxi.log")
@patch("saltext.vmware.modules.esxi.salt.exceptions.SaltException")
def test_pre_check_success(salt_exception_mock, log_mock, create_esx_config_mock):
    esx_config_mock = Mock()
    create_esx_config_mock.return_value = esx_config_mock
    esx_config_mock.precheck_desired_state.return_value = "Pre-check response"

    result = esxi.pre_check(profile, cluster_paths, desired_state_spec, esx_config_mock)

    log_mock.debug.assert_called_with("Precheck %s", desired_state_spec)
    esx_config_mock.precheck_desired_state.assert_called_with(
        desired_state_spec=desired_state_spec, cluster_paths=cluster_paths
    )
    assert result == "Pre-check response"
    salt_exception_mock.assert_not_called()


@patch("saltext.vmware.modules.esxi.log")
@patch("saltext.vmware.modules.esxi.salt.exceptions.SaltException")
def test_pre_check_failure(salt_exception_mock, log_mock, create_esx_config_mock):
    esx_config_mock = Mock()
    create_esx_config_mock.return_value = esx_config_mock
    esx_config_mock.precheck_desired_state.side_effect = Exception("Test error")

    with pytest.raises(salt_exception_mock):
        esxi.pre_check(profile, cluster_paths, desired_state_spec, esx_config_mock)

    log_mock.debug.assert_called_with("Precheck %s", desired_state_spec)
    esx_config_mock.precheck_desired_state.assert_called_with(
        desired_state_spec=desired_state_spec, cluster_paths=cluster_paths
    )
    log_mock.error.assert_called_with("Pre-check failed: %s", "Test error")


@patch("saltext.vmware.modules.esxi.log")
@patch("saltext.vmware.modules.esxi.salt.exceptions.SaltException")
def test_remediate_success(salt_exception_mock, log_mock, create_esx_config_mock):
    esx_config_mock = Mock()
    create_esx_config_mock.return_value = esx_config_mock
    esx_config_mock.remediate_with_desired_state.return_value = "Remediation response"

    result = esxi.remediate(profile, cluster_paths, desired_state_spec, esx_config_mock)

    log_mock.debug.assert_called_with("Remediate %s", desired_state_spec)
    esx_config_mock.remediate_with_desired_state.assert_called_with(
        desired_state_spec=desired_state_spec, cluster_paths=cluster_paths
    )
    assert result == "Remediation response"
    salt_exception_mock.assert_not_called()


@patch("saltext.vmware.modules.esxi.log")
@patch("saltext.vmware.modules.esxi.salt.exceptions.SaltException")
def test_remediate_failure(salt_exception_mock, log_mock, create_esx_config_mock):
    esx_config_mock = Mock()
    create_esx_config_mock.return_value = esx_config_mock
    esx_config_mock.remediate_with_desired_state.side_effect = Exception("Test error")

    with pytest.raises(salt_exception_mock):
        esxi.remediate(profile, cluster_paths, desired_state_spec, esx_config_mock)

    log_mock.debug.assert_called_with("Remediate %s", desired_state_spec)
    esx_config_mock.remediate_with_desired_state.assert_called_with(
        desired_state_spec=desired_state_spec, cluster_paths=cluster_paths
    )
    log_mock.error.assert_called_with("Remediation failed: %s", "Test error")
