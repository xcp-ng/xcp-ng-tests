def perform_install(*, iso, host_vm):
    host_vm.insert_cd(iso)

    host_vm.eject_cd()
