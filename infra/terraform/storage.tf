resource "yandex_compute_disk" "boot" {
  name      = "findme-photo-boot"
  folder_id = var.folder_id
  zone      = var.zone
  type      = "network-ssd"
  size      = var.boot_disk_gb
  image_id  = var.ubuntu_image_id
}

resource "yandex_compute_disk" "postgres" {
  name      = "findme-photo-postgres"
  folder_id = var.folder_id
  zone      = var.zone
  type      = "network-ssd"
  size      = var.postgres_disk_gb

  lifecycle {
    prevent_destroy = true
  }
}
