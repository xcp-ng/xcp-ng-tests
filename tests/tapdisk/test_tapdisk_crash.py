def test_snapshot(vm_on_local_sr):
    #We need started VM since we need a tapdisk instance
    vm = vm_on_local_sr
    snap1 = vm.snapshot()
    snap2 = vm.snapshot()
    snap1.destroy()
    snap2.destroy()
    snap3 = vm.snapshot()
    snap3.destroy()
