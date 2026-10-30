mock_provider "yandex" {}

run "one_recovery_host" {
  command = apply

  variables {
    cloud_id         = "b1gmcsmr51o5kvp86l55"
    folder_id        = "b1g2qttgfhb4gdunvlge"
    zone             = "ru-central1-d"
    ubuntu_image_id  = "fd8a30ptg5tdapq35dlb"
    ssh_public_key   = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN test"
    ssh_cidrs        = ["203.0.113.8/32"]
    boot_disk_gb     = 30
    postgres_disk_gb = 40
  }

  assert {
    condition     = yandex_compute_instance.app.resources[0].cores == 2 && yandex_compute_instance.app.resources[0].core_fraction == 20 && yandex_compute_instance.app.resources[0].memory == 1
    error_message = "The recovery host shape changed."
  }

  assert {
    condition     = yandex_compute_instance.app.boot_disk[0].auto_delete == false && alltrue([for disk in yandex_compute_instance.app.secondary_disk : disk.auto_delete == false])
    error_message = "Boot and PostgreSQL disks must survive VM deletion."
  }

  assert {
    condition     = yandex_compute_disk.postgres.size == 40 && yandex_compute_disk.boot.size == 30
    error_message = "Reviewed disk inputs must reach distinct resources."
  }

  assert {
    condition     = length(yandex_vpc_security_group.vm.ingress) == 2 && toset([for rule in yandex_vpc_security_group.vm.ingress : rule.port]) == toset([80, 22])
    error_message = "Only HTTP and reviewed SSH sources should be publicly reachable."
  }

  assert {
    condition     = yandex_resourcemanager_folder_iam_member.backup_agent.role == "backup.user"
    error_message = "Temporary recovery agent must have only backup.user on its VM identity."
  }
}
