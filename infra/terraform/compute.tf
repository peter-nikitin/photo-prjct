resource "yandex_compute_instance" "app" {
  name               = "findme-photo-app"
  folder_id          = var.folder_id
  zone               = var.zone
  platform_id        = "standard-v3"
  service_account_id = yandex_iam_service_account.backup_agent.id

  resources {
    cores         = 2
    core_fraction = 20
    memory        = 1
  }

  boot_disk {
    disk_id     = yandex_compute_disk.boot.id
    auto_delete = false
  }

  secondary_disk {
    disk_id     = yandex_compute_disk.postgres.id
    auto_delete = false
    device_name = "pgdata"
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.core_d.id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.vm.id]
  }

  metadata = {
    enable-oslogin = "false"
    user-data = templatefile("${path.module}/cloud-init.yml.tftpl", {
      ssh_public_key = trimspace(var.ssh_public_key)
    })
    serial-port-enable = "1"
  }

  depends_on = [yandex_resourcemanager_folder_iam_member.backup_agent]
}

# The static service address is still associated with the stopped legacy VM.
# Adopt that VM only long enough to release the NAT address before the NLB claims it.
import {
  to = yandex_compute_instance.legacy_ip_holder
  id = "epdr5g3p24tdns9890nr"
}

resource "yandex_compute_instance" "legacy_ip_holder" {
  name                      = "dev-photo-prjct"
  folder_id                 = var.folder_id
  zone                      = "ru-central1-b"
  platform_id               = "standard-v3"
  service_account_id        = "aje62dg0p7tpn6tqu67d"
  allow_stopping_for_update = false

  resources {
    cores         = 8
    core_fraction = 100
    memory        = 16
  }

  boot_disk {
    disk_id     = data.yandex_compute_disk.legacy_boot.id
    auto_delete = true
  }

  network_interface {
    subnet_id          = "e2l18k26thgtq8vobbq7"
    ip_address         = "10.129.0.34"
    nat                = false
    security_group_ids = ["enp2dpjqa3i8cttdsnpp"]
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [metadata]
  }
}
