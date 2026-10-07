"""Syntax checks shared by API schemas and config writers; no shell expansion."""
import ipaddress
import re

_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_CATEGORY = re.compile(r"!?[A-Za-z0-9][A-Za-z0-9_.-]*(?:@!?[A-Za-z0-9][A-Za-z0-9_.-]*)?")


def validate_host(value: str) -> str:
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if not value or len(value) > 253 or not all(_LABEL.fullmatch(label) for label in value.split('.')):
        raise ValueError('Укажите IP-адрес или доменное имя без протокола, порта и пути')
    return value.lower()


def routing_match_type(value: str) -> str:
    value = value.strip()
    prefix, _, argument = value.partition(':')
    if prefix in ('domain', 'full'):
        validate_host(argument)
        try:
            ipaddress.ip_address(argument)
        except ValueError:
            return 'domain'
        raise ValueError('Для IP используйте адрес или CIDR без domain:/full:')
    if prefix in ('geosite', 'geoip'):
        if len(argument) > 128 or not _CATEGORY.fullmatch(argument):
            raise ValueError('Укажите допустимую категорию geosite:/geoip:')
        return 'domain' if prefix == 'geosite' else 'ip'
    try:
        ipaddress.ip_network(value, strict=False)
        return 'ip'
    except ValueError:
        raise ValueError('Используйте domain:, full:, geosite:, geoip: или IP/CIDR') from None
