mock_provider "yandex" {}

run "one_recovery_host" {
  command = apply

  override_resource {
    target = yandex_compute_instance.legacy_ip_holder
    values = {
      id = "epdr5g3p24tdns9890nr"
      network_interface = {
        nat                = false
        ip_address         = "10.129.0.34"
        subnet_id          = "e2l18k26thgtq8vobbq7"
        security_group_ids = ["enp2dpjqa3i8cttdsnpp"]
      }
    }
  }

  override_resource {
    target = yandex_vpc_address.site
    values = {
      id = "e2lq0aldvqrp4uebr75l"
      external_ipv4_address = {
        address = "111.88.151.64"
        zone_id = "ru-central1-b"
      }
    }
  }

  override_data {
    target = data.yandex_compute_disk.legacy_boot
    values = {
      id   = "epdrviptgj09r7ac478d"
      size = 100
      type = "network-ssd"
    }
  }

  variables {
    cloud_id                          = "b1gmcsmr51o5kvp86l55"
    folder_id                         = "b1g2qttgfhb4gdunvlge"
    infrastructure_service_account_id = "ajema849qjho2lgjaqvo"
    zone                              = "ru-central1-d"
    ubuntu_image_id                   = "fd8a30ptg5tdapq35dlb"
    ssh_public_key                    = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN test"
    ssh_cidrs                         = ["203.0.113.8/32"]
    boot_disk_gb                      = 30
    postgres_disk_gb                  = 40
  }

  assert {
    condition     = yandex_compute_instance.app.resources[0].cores == 2 && yandex_compute_instance.app.resources[0].core_fraction == 20 && yandex_compute_instance.app.resources[0].memory == 1
    error_message = "The recovery host shape changed."
  }

  assert {
    condition     = yandex_compute_instance.app.metadata["enable-oslogin"] == "false"
    error_message = "The recovery host must keep passwordless SSH login enabled for Ansible."
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
    condition     = length(yandex_vpc_security_group.vm.ingress) == 4 && toset([for rule in yandex_vpc_security_group.vm.ingress : rule.port]) == toset([80, 443, 22]) && anytrue([for rule in yandex_vpc_security_group.vm.ingress : rule.predefined_target == "loadbalancer_healthchecks"])
    error_message = "Only HTTP, HTTPS, and reviewed SSH sources should be publicly reachable."
  }

  assert {
    condition     = yandex_resourcemanager_folder_iam_member.backup_agent.role == "backup.user"
    error_message = "Temporary recovery agent must have only backup.user on its VM identity."
  }

  assert {
    condition     = yandex_compute_instance.legacy_ip_holder.network_interface[0].nat == false
    error_message = "The stopped legacy VM must release the reserved frontend address without replacement."
  }

  assert {
    condition     = data.yandex_compute_disk.legacy_boot.size == 100 && yandex_compute_instance.legacy_ip_holder.boot_disk[0].disk_id == data.yandex_compute_disk.legacy_boot.id
    error_message = "The imported legacy VM must retain its existing boot disk."
  }

  assert {
    condition     = yandex_vpc_address.site.id == "e2lq0aldvqrp4uebr75l"
    error_message = "Terraform must adopt and preserve the existing reserved frontend address."
  }

  assert {
    condition = (
      toset([for listener in yandex_lb_network_load_balancer.site.listener : listener.port]) == toset([22, 80, 443]) &&
      alltrue([for listener in yandex_lb_network_load_balancer.site.listener : listener.target_port == listener.port])
    )
    error_message = "The NLB must forward SSH, HTTP, and HTTPS to the matching backend ports."
  }

  assert {
    condition     = yandex_lb_network_load_balancer.site.deletion_protection
    error_message = "The externally visible NLB must be protected from accidental deletion."
  }

  assert {
    condition = alltrue([
      for target in yandex_lb_network_load_balancer.site.attached_target_group : alltrue([
        for check in target.healthcheck : (
          check.tcp_options[0].port == 22 && check.interval == 5 && check.timeout == 2
        )
      ])
    ])
    error_message = "Until the application is deployed, backend health must use SSH TCP/22."
  }

  assert {
    condition     = output.ssh_host == "111.88.151.64"
    error_message = "Automation must connect through the stable NLB address."
  }

  assert {
    condition     = yandex_resourcemanager_folder_iam_member.infrastructure_lb_editor.role == "load-balancer.editor"
    error_message = "The existing infrastructure CI identity must have permission to manage the NLB."
  }
}
