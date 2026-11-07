mock_provider "yandex" {}

override_resource {
  target = yandex_vpc_address.edge
  values = { external_ipv4_address = { address = "111.88.151.64" } }
}
override_resource {
  target = yandex_compute_instance.legacy_holder
}


run "one_recovery_host" {
  command = apply

  variables {
    cloud_id             = "b1gmcsmr51o5kvp86l55"
    folder_id            = "b1g2qttgfhb4gdunvlge"
    zone                 = "ru-central1-d"
    ubuntu_image_id      = "fd8p50cvjmb6fo0ik4hk"
    ssh_public_key       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN test"
    admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN admin"
    ssh_cidrs            = ["203.0.113.8/32"]
    boot_disk_gb         = 30
    postgres_disk_gb     = 10
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
    condition = (
      yandex_compute_disk.postgres.size == 10 &&
      yandex_compute_disk.boot.size == 30 &&
      yandex_compute_disk.postgres.type == "network-ssd" &&
      yandex_compute_disk.boot.type == "network-ssd"
    )
    error_message = "Both reviewed disk sizes and SSD types must reach distinct resources."
  }

  assert {
    condition     = length(yandex_vpc_security_group.vm.ingress) == 4 && toset([for rule in yandex_vpc_security_group.vm.ingress : rule.port]) == toset([80, 443, 8081, 22])
    error_message = "Expected HTTP, HTTPS, health check and SSH rules."
  }

  assert {
    condition     = toset([for l in yandex_lb_network_load_balancer.edge.listener : l.port]) == toset([80, 443]) && alltrue([for l in yandex_lb_network_load_balancer.edge.listener : l.protocol == "tcp" && l.target_port == l.port && one(l.external_address_spec).address == "111.88.151.64"])
    error_message = "NLB must expose only TCP 80/443 on the reserved address."
  }

  assert {
    condition     = one(yandex_lb_target_group.app.target).subnet_id == yandex_vpc_subnet.core_d.id && one(yandex_lb_target_group.app.target).address == yandex_compute_instance.app.network_interface[0].ip_address && one(one(yandex_lb_network_load_balancer.edge.attached_target_group).healthcheck).http_options[0].port == 8081 && one(one(yandex_lb_network_load_balancer.edge.attached_target_group).healthcheck).http_options[0].path == "/health/"
    error_message = "NLB must target the app and check readiness on 8081."
  }

  assert {
    condition     = one([for rule in yandex_vpc_security_group.vm.ingress : rule if rule.port == 8081]).predefined_target == "loadbalancer_healthchecks" && output.ssh_host == yandex_compute_instance.app.network_interface[0].nat_ip_address
    error_message = "Health checks are restricted and SSH stays direct."
  }

  assert {
    condition     = yandex_resourcemanager_folder_iam_member.backup_agent.role == "backup.user"
    error_message = "Temporary recovery agent must have only backup.user on its VM identity."
  }

  assert {
    condition     = strcontains(yandex_compute_instance.app.metadata["user-data"], "name: deploy") && strcontains(yandex_compute_instance.app.metadata["user-data"], "name: petrnikitin") && yandex_compute_instance.app.metadata["enable-oslogin"] == "false"
    error_message = "Cloud-init must provision both separate key-only SSH identities with OS Login disabled."
  }
}
