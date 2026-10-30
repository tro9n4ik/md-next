"""Общий проверяющий TLS-контекст для короткоживущих сетевых проб."""
import ssl
from functools import lru_cache

import certifi


@lru_cache(maxsize=1)
def probe_tls_context():
    # Повторная загрузка CA при каждой пробе увеличивает native TLS allocations.
    # Не разделяем cookies, клиенты или соединения; только проверенное хранилище CA.
    return ssl.create_default_context(cafile=certifi.where())
