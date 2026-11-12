terraform {
  required_version = "= 1.17.0"

  required_providers {
    yandex = {
      source  = "yandex-cloud/yandex"
      version = "= 0.240.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.72.0"
    }
  }
}

provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
  zone      = var.zone
}

provider "aws" {
  alias                       = "postbox"
  region                      = "ru-central1"
  access_key                  = var.postbox_access_key
  secret_key                  = var.postbox_secret_key
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  skip_region_validation      = true
  endpoints {
    sesv2 = "https://postbox.cloud.yandex.net"
  }
}
