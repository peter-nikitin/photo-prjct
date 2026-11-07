resource "yandex_vpc_network" "core" {
  name      = "findme-photo-core"
  folder_id = var.folder_id
}

resource "yandex_vpc_subnet" "core_d" {
  name           = "findme-photo-core-d"
  folder_id      = var.folder_id
  zone           = var.zone
  network_id     = yandex_vpc_network.core.id
  v4_cidr_blocks = ["10.240.0.0/24"]
}

resource "yandex_vpc_security_group" "vm" {
  name       = "findme-photo-single-vm"
  folder_id  = var.folder_id
  network_id = yandex_vpc_network.core.id

  ingress {
    protocol       = "TCP"
    description    = "HTTP traffic from the NLB."
    port           = 80
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    protocol       = "TCP"
    description    = "HTTPS traffic from the NLB."
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    protocol          = "TCP"
    description       = "NLB readiness probes only."
    port              = 8081
    predefined_target = "loadbalancer_healthchecks"
  }

  ingress {
    protocol       = "TCP"
    description    = "Operator and CI SSH sources reviewed in project inputs."
    port           = 22
    v4_cidr_blocks = var.ssh_cidrs
  }

  egress {
    protocol       = "ANY"
    description    = "Packages, GHCR, existing S3 and Cloud Backup agent endpoints."
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

# Existing reserved address remains managed and protected.
import {
  to = yandex_vpc_address.edge
  id = "e2lq0aldvqrp4uebr75l"
}

resource "yandex_vpc_address" "edge" {
  name        = "dev-photo-prjct-static-ip"
  description = "Статический публичный IP для dev-photo-prjct"
  folder_id   = var.folder_id
  external_ipv4_address {
    zone_id = "ru-central1-b"
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "yandex_lb_target_group" "app" {
  depends_on = [yandex_resourcemanager_folder_iam_member.infrastructure_nlb]

  name      = "findme-photo-app"
  folder_id = var.folder_id
  target {
    subnet_id = yandex_vpc_subnet.core_d.id
    address   = yandex_compute_instance.app.network_interface[0].ip_address
  }
}

resource "yandex_lb_network_load_balancer" "edge" {
  name                = "findme-photo-edge"
  folder_id           = var.folder_id
  deletion_protection = true

  dynamic "listener" {
    for_each = toset([80, 443])
    content {
      name        = "tcp-${listener.value}"
      port        = listener.value
      target_port = listener.value
      protocol    = "tcp"
      external_address_spec {
        address = yandex_vpc_address.edge.external_ipv4_address[0].address
      }
    }
  }

  attached_target_group {
    target_group_id = yandex_lb_target_group.app.id
    healthcheck {
      name = "health"
      http_options {
        port = 8081
        path = "/health/"
      }
    }
  }
}
