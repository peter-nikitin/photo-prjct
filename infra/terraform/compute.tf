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
    user-data = templatefile("${path.module}/cloud-init.yml.tftpl", {
      ssh_public_key = trimspace(var.ssh_public_key)
    })
    serial-port-enable = "1"
  }

  depends_on = [yandex_resourcemanager_folder_iam_member.backup_agent]
}
