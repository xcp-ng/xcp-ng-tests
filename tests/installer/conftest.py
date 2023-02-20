import logging
import os
import pytest
import sys
sys.path.append(f"{os.path.abspath(os.path.dirname(__file__))}/..") # noqa
from lib.commands import SSHCommandFailed

def check_vm(host, vm):
    check_vm_ressources(host, vm)
    check_vm_boot_order(vm)
    check_vm_nested_on(vm)
    check_vm_uefi_vga_driver(vm)

def check_vm_ressources(host, vm):
    number_memory = int(vm.param_get('memory-static-max'))
    vdi_uuid = vm.vdi_uuids()[0]
    # TODO: quid if multiples vdi on a vm ?
    number_harddisk = int(host.xe('vdi-param-get', {'param-name': 'virtual-size',  'uuid': vdi_uuid}))
    if number_memory < 2147483648:
        raise Exception(f"not enough RAM on the VM: `{number_memory}`. 2 Go is the minimal.")
    
    if number_harddisk < 64424509440:
        raise Exception(f"not enough space on the harddisk on the VM: `{number_harddisk}`. 60 Go is the minimal.")

def check_vm_boot_order(vm):
    if vm.param_get('HVM-boot-params', 'order') != 'ncd':
        vm.param_set('HVM-boot-params', 'order', 'ncd')

def check_vm_nested_on(vm):
    try:
        is_nested = vm.param_get('platform', 'exp-nested-hvm')
    except SSHCommandFailed:
    # if nested was never activate, the field dosn't exist. It's like it was a False value.
        is_nested = False
    if not is_nested:
        vm.param_set('platform', 'exp-nested-hvm', 'true')

def check_vm_uefi_vga_driver(vm):
    if vm.param_get('HVM-boot-params', 'firmware') == 'uefi':
        try:
            is_vga = vm.param_get('platform', 'vga')
        except SSHCommandFailed:
        # if nested was never activate, the field dosn't exist. It's like it was a False value.
            is_vga = ''
        if is_vga != 'std':
            vm.param_set('platform', 'vga', 'std')

def matches_rule(line, rule):
    if not line.startswith(rule['loglevel']) or not line.endswith(f"rc {rule['rc']}"):
        return False
    for substring in rule['substrings']:
        if substring not in line:
            return False
    return True
