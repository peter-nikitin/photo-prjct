resource "yandex_cdn_origin_group" "gallery" {
  name          = "findme-gallery-image-origin"
  folder_id     = var.folder_id
  provider_type = "ourcdn"
  use_next      = true
  origin {
    source  = "img-origin.findme-photo.ru"
    enabled = true
    backup  = false
  }
  lifecycle { prevent_destroy = true }
}
resource "yandex_cm_certificate" "gallery" {
  name                = "findme-gallery-cdn"
  description         = "FindMe Photo gallery CDN client TLS"
  folder_id           = var.folder_id
  domains             = ["img.findme-photo.ru"]
  deletion_protection = true
  managed {
    challenge_type = "DNS_CNAME"
  }
  lifecycle { prevent_destroy = true }
}
