import pytest
from pydantic import ValidationError
from app.api.nodes import NodeRegister
from app.schemas.routing import RoutingRuleCreate


@pytest.mark.parametrize('value', ['domain:example.com;id', 'full:$(id)', 'domain:example.com\ninclude x;', 'geosite:../../secret', 'geoip:ru;id', '192.0.2.1/999', 'domain:-example.com', 'full:https://example.com'])
def test_invalid_routing_input_is_rejected_by_api_schema(value):
    with pytest.raises(ValidationError):
        RoutingRuleCreate(domain_or_ip=value, action='direct')


@pytest.mark.parametrize('value', ['domain:example.com', 'full:localhost', '192.0.2.0/24', '2001:db8::/32', 'geoip:!ru', 'geosite:google@!cn'])
def test_valid_routing_syntax(value):
    assert RoutingRuleCreate(domain_or_ip=value, action='direct').domain_or_ip == value


@pytest.mark.parametrize('host', ['$(id)', '-example.com', 'example.com:443', 'example.com/path', 'example.com\nsecret'])
def test_node_host_rejects_shell_and_config_syntax(host):
    with pytest.raises(ValidationError):
        NodeRegister(token='synthetic-test-token', host=host, port=443)


@pytest.mark.parametrize('port', [0, -1, 65536])
def test_node_port_is_bounded(port):
    with pytest.raises(ValidationError):
        NodeRegister(token='synthetic-test-token', host='192.0.2.10', port=port)


def test_node_ipv6_and_domain_are_supported():
    assert NodeRegister(token='synthetic-test-token', host='2001:db8::1', port=443).host == '2001:db8::1'
    assert NodeRegister(token='synthetic-test-token', host='Node.Example.com', port=443).host == 'node.example.com'
