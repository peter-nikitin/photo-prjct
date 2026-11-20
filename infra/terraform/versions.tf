terraform {
  required_version = "= 1.17.0"

  required_providers {
    findmecdn = {
      source  = "terraform.findme-photo.ru/findme/findmecdn"
      version = "= 0.1.0"
    }
    yandex = {
      source  = "yandex-cloud/yandex"
      version = "= 0.240.0"
    }
  }
}

provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
  zone      = var.zone
}
