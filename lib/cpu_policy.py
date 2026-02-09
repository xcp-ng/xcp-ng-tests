import subprocess

NO_SUBLEAF = 0xffffffff

def parse_xen_policy(text):
    policy = {}
    current_policy = None
    mode = None  # None | "cpuid" | "msr"

    lines = text.splitlines()

    for line in lines:
        line = line.rstrip()

        # ---- Policy header ----
        if "policy:" in line:
            # Example: "Raw policy: 32 leaves, 2 MSRs"
            name, rest = line.split("policy:", 1)
            name = name.strip()

            parts = rest.strip().split(",")
            leaves = int(parts[0].split()[0])
            msrs = int(parts[1].split()[0])

            current_policy = {
                "leaves": leaves,
                "msrs": msrs,
                "cpuid": {},
                "msr": {}
            }
            policy[name] = current_policy
            mode = None
            continue

        if current_policy is None:
            continue

        # ---- Section switches ----
        if line.strip() == "CPUID:":
            mode = "cpuid"
            continue

        if line.strip() == "MSRs:":
            mode = "msr"
            continue

        # Skip table headers
        if "leaf" in line or "index" in line or not line.strip():
            continue

        # ---- CPUID parsing ----
        if mode == "cpuid":
            # Example:
            # 00000004:00000003 -> 1c03c163:02c0003f:00001fff:00000006
            left, right = line.split("->")
            leaf_hex, subleaf_hex = left.strip().split(":")
            eax, ebx, ecx, edx = right.strip().split(":")

            key = (int(leaf_hex, 16), int(subleaf_hex, 16))
            current_policy["cpuid"][key] = {
                "eax": int(eax, 16),
                "ebx": int(ebx, 16),
                "ecx": int(ecx, 16),
                "edx": int(edx, 16),
            }
            continue

        # ---- MSR parsing ----
        if mode == "msr":
            # Example:
            # 0000010a -> 400000000c000000
            idx_hex, val_hex = line.split("->")
            idx = int(idx_hex.strip(), 16)
            val = int(val_hex.strip(), 16)
            current_policy["msr"][idx] = val
            continue

    return policy

def get_cpu_policy(host):
    return parse_xen_policy(host.ssh(["xen-cpuid", "--policy"]))