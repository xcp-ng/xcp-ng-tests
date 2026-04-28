from dataclasses import dataclass

from lib.host import Host

NO_SUBLEAF = 0xffffffff

@dataclass(frozen=True)
class CpuidRegisters:
    eax: int
    ebx: int
    ecx: int
    edx: int

class CpuPolicy:
    """A specific CPU policy (PV, HVM, ...)"""
    # (leaf, subleaf) -> (eax, ebx, ecx, edx)
    cpuid: dict[tuple[int, int], CpuidRegisters]
    # msr -> val
    msr: dict[int, int]

    def __init__(self, cpuid: dict[tuple[int, int], CpuidRegisters], msr: dict[int, int]):
        self.cpuid = cpuid
        self.msr = msr

class HostCpuPolicy:
    """All CPU policies of a host (CPUID, specific MSRs)"""
    policies: dict[str, CpuPolicy] = {}

    def __init__(self, host: Host):
        text = host.ssh("xen-cpuid --policy")
        current_policy: CpuPolicy | None = None
        mode: Literal["cpuid", "msr"] | None = None

        lines = text.splitlines()

        for line in lines:
            line = line.rstrip()

            # ---- Policy header ----
            if "policy:" in line:
                # Example: "Raw policy: 32 leaves, 2 MSRs"
                name, rest = line.split("policy:", 1)
                name = name.strip()

                self.policies[name] = CpuPolicy(dict(), dict())
                current_policy = self.policies[name]
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
                current_policy.cpuid[key] = CpuidRegisters(
                    int(eax, 16),
                    int(ebx, 16),
                    int(ecx, 16),
                    int(edx, 16)
                )
                continue

            # ---- MSR parsing ----
            if mode == "msr":
                # Example:
                # 0000010a -> 400000000c000000
                idx_hex, val_hex = line.split("->")
                idx = int(idx_hex.strip(), 16)
                val = int(val_hex.strip(), 16)
                current_policy.msr[idx] = val
                continue
