packer {
  required_plugins {
    xenserver = {
      version = ">= v0.11.4"
      source  = "github.com/vatesfr/xenserver"
    }
  }
}

variable "xen_host" {
  type        = string
  description = "XCP-ng pool master hostname or IP"
}

variable "xen_username" {
  type        = string
  description = "XCP-ng SSH username"
  default     = "root"
}

variable "xen_password" {
  type        = string
  description = "XCP-ng SSH password"
  sensitive   = true
}

variable "network_name" {
  type        = string
  description = "Name of the XCP-ng network to attach the VM to"
  default     = "Pool-wide network associated with eth0"
}

variable "sr_name" {
  type        = string
  description = "Name of the SR to store the VM disk on (defaults to pool default)"
  default     = ""
}

variable "sr_iso_name" {
  type        = string
  description = "Name of the ISO SR to upload the Alpine ISO to (defaults to pool default)"
  default     = ""
}

variable "root_password" {
  type        = string
  description = "Temporary root password set during the build"
  default     = "vateslab"
  sensitive   = true
}

source "xenserver-iso" "alpine" {
  remote_host     = var.xen_host
  remote_username = var.xen_username
  remote_password = var.xen_password

  vm_name         = "alpine-3.23-uefi"
  vm_description  = "Alpine Linux 3.23.4 minimal UEFI - built with Packer"
  vcpus_max       = 1
  vcpus_atstartup = 1
  vm_memory       = 256
  disk_size       = 512

  firmware       = "uefi"
  clone_template = "Other install media"

  network_names = [var.network_name]

  sr_name     = var.sr_name
  sr_iso_name = var.sr_iso_name

  iso_url      = "https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/x86_64/alpine-standard-3.23.4-x86_64.iso"
  iso_checksum = "sha256:cfef39c7954f7c4447bcb321b9f4a1cef834536a321309d2c31275d9f2475a4e"

  http_directory = "http_files"
  boot_wait      = "30s"
  boot_command = [
    "root<enter>",
    "<wait3>",
    "ifconfig eth0 up && udhcpc -i eth0<enter><wait5>",
    # "USE<leftShiftOn>-<leftShiftOff>EFI=1 SWAP<leftShiftOn>-<leftShiftOff>SIZE=0 ERASE<leftShiftOn>-<leftShiftOff>DISKS=/dev/xvda setup-alpine -f http://{{.HTTPIP}}:{{.HTTPPort}}/answers.txt<enter>",
    "USE<leftShiftOn>-<leftShiftOff>EFI=1 SWAP<leftShiftOn>-<leftShiftOff>SIZE=0 ERASE<leftShiftOn>-<leftShiftOff>DISKS=/dev/xvda setup-alpine -f https://raw.githubusercontent.com/xcp-ng/xcp-ng-tests/refs/heads/gln/packer-alpine-uefi-xcpng-loun/packer/http_files/answers.txt<enter>",
    "<wait10>",
    "${var.root_password}<enter>",
    "<wait>",
    "${var.root_password}<enter>",
    "<wait60>",
    "apk add --no-cache xe-guest-utilities<enter>",
    "rc-update add xe-guest-utilities default<enter>",
    # "rc-service xe-guest-utilities start<enter>",
    # Remove log files
    "find /var/log -type f -exec truncate -s 0 {} \\;<enter>",
    # Remove temporary files
    "rm -rf /tmp/* /var/tmp/*<enter>",
    # Clear machine-id so each clone gets a unique one
    "echo > /etc/machine-id<enter>",
    # Remove history
    "rm -f /root/.ash_history<enter>",
    "<wait3>",
  ]

  communicator = "none"
  ssh_username     = "root"
  ssh_password     = var.root_password
  ssh_wait_timeout = "10m"

  install_timeout  = "30m"
  output_directory = "export"
  keep_vm          = "never"
  # format           = "xva_zstd"
}

build {
  sources = ["xenserver-iso.alpine"]
}
