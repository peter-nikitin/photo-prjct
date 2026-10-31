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
    description    = "HTTP backend port for the future NLB; application is not started in this stage."
    port           = 80
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    protocol       = "TCP"
    description    = "HTTPS backend port for the stable NLB frontend."
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    protocol       = "TCP"
    description    = "Operator and CI SSH sources reviewed in project inputs."
    port           = 22
    v4_cidr_blocks = var.ssh_cidrs
  }

  ingress {
    protocol          = "TCP"
    description       = "Network Load Balancer TCP/22 health checks."
    port              = 22
    predefined_target = "loadbalancer_healthchecks"
  }

  egress {
    protocol       = "ANY"
    description    = "Packages, GHCR, existing S3 and Cloud Backup agent endpoints."
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}
