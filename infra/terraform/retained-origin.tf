# Сохраняемые зависимости прежнего origin. Старая VM исключена из целевой конфигурации.
resource "yandex_vpc_network" "origin_default" {
  name        = "default"
  description = "Auto-created network"
  folder_id   = var.folder_id
  lifecycle { prevent_destroy = true }
}
resource "yandex_vpc_subnet" "origin_b" {
  name           = "default-ru-central1-b"
  description    = "Auto-created default subnet for zone ru-central1-b in default"
  folder_id      = var.folder_id
  network_id     = yandex_vpc_network.origin_default.id
  zone           = "ru-central1-b"
  v4_cidr_blocks = ["10.129.0.0/24"]
  lifecycle { prevent_destroy = true }
}
resource "yandex_vpc_security_group" "image_origin" {
  name       = "findme-gallery-image-origin"
  folder_id  = var.folder_id
  network_id = yandex_vpc_network.origin_default.id
  ingress {
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = ["104.238.27.164/32"]
  }
  ingress {
    protocol       = "TCP"
    port           = 80
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    protocol       = "TCP"
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    protocol          = "TCP"
    port              = 22
    security_group_id = "enpclrep8uilre076c6q"
  }
  ingress {
    protocol          = "TCP"
    port              = 22
    security_group_id = "enp2dpjqa3i8cttdsnpp"
  }
  egress {
    protocol       = "TCP"
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    protocol       = "TCP"
    port           = 53
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    protocol       = "UDP"
    port           = 53
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
  lifecycle { prevent_destroy = true }
}
resource "yandex_vpc_address" "image_origin" {
  name      = "findme-gallery-image-origin"
  folder_id = var.folder_id
  external_ipv4_address { zone_id = "ru-central1-b" }
  lifecycle { prevent_destroy = true }
}
resource "yandex_compute_disk" "image_origin" {
  folder_id = var.folder_id
  zone      = "ru-central1-b"
  type      = "network-hdd"
  size      = 20
  image_id  = "fd8nj6iro13qffg31not"
  lifecycle { prevent_destroy = true }
}
