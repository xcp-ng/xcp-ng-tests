# Requirements:
# - one XCP-ng host (--host) (>= 8.3 for IPv6 test)

def test_management_disable_address_type(host):
    management_pif = host.management_pif()
    type = management_pif.param_get("primary-address-type").lower()

    host.xe("host-management-disable")
    assert management_pif.param_get("primary-address-type").lower() == type

    host.xe("host-management-reconfigure", {"pif-uuid": management_pif.uuid})
    assert management_pif.param_get("primary-address-type").lower() == type
