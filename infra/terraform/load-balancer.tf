import {
  to = yandex_vpc_address.site
  id = "e2lq0aldvqrp4uebr75l"
}

resource "yandex_vpc_address" "site" {
  name      = "dev-photo-prjct-static-ip"
  folder_id = var.folder_id

  external_ipv4_address {
    zone_id = "ru-central1-b"
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [description]
  }

}

resource "yandex_lb_target_group" "app" {
  name      = "findme-photo-app"
  folder_id = var.folder_id

  depends_on = [yandex_resourcemanager_folder_iam_member.infrastructure_lb_editor]

  target {
    address   = yandex_compute_instance.app.network_interface[0].ip_address
    subnet_id = yandex_vpc_subnet.core_d.id
  }
}

resource "yandex_lb_network_load_balancer" "site" {
  name                = "findme-photo-public"
  folder_id           = var.folder_id
  type                = "external"
  deletion_protection = true

  attached_target_group {
    target_group_id = yandex_lb_target_group.app.id

    healthcheck {
      name     = "ssh-tcp-22"
      interval = 5
      timeout  = 2

      tcp_options {
        port = 22
      }
    }
  }

  dynamic "listener" {
    for_each = toset([22, 80, 443])
    content {
      name        = "tcp-${listener.value}"
      port        = listener.value
      protocol    = "tcp"
      target_port = listener.value

      external_address_spec {
        address = yandex_vpc_address.site.external_ipv4_address[0].address
      }
    }
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    yandex_resourcemanager_folder_iam_member.infrastructure_lb_editor,
    yandex_compute_instance.legacy_ip_holder,
  ]
}
