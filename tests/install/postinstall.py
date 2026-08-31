from pathlib import Path

def make_postinstall_script() -> str:

    from data import ARP_SERVER, TEST_SSH_PUBKEY

    parent_dir = Path(__file__).parent

    test_pingpxe_sh = (parent_dir / "test-pingpxe.sh").read_text()
    test_pingpxe_service = (parent_dir / "test-pingpxe.service").read_text().format(ARP_SERVER=ARP_SERVER)
    test_pingpxe_s12 = (parent_dir / "S12test-pingpxe.sh").read_text().format(ARP_SERVER=ARP_SERVER)
    postinstall = (parent_dir / "postinstall.sh").read_text()
    return postinstall.format(
        TEST_PINGPXE_SH=test_pingpxe_sh,
        TEST_PINGPXE_SERVICE=test_pingpxe_service,
        TEST_PINGPXE_S12=test_pingpxe_s12,
        TEST_SSH_PUBKEY=TEST_SSH_PUBKEY,
    )
