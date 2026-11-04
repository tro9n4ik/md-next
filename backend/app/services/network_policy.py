"""Размер внутреннего IP-пакета для управляемых туннелей панели."""
TUNNEL_MTU = 1380
IPV4_MSS = TUNNEL_MTU - 40
IPV6_MSS = TUNNEL_MTU - 60
