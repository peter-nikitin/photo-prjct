terraform {
  required_providers {
    findmecdn = {
      source = "terraform.findme-photo.ru/findme/findmecdn"
    }
    yandex = {
      source = "yandex-cloud/yandex"
    }
  }
}

variable "folder_id" {
  type = string
}

variable "media_delivery_bucket" {
  type = string
}

variable "secure_key" {
  type      = string
  default   = null
  ephemeral = true
  sensitive = true
}

variable "origin_auth" {
  type      = string
  default   = null
  ephemeral = true
  sensitive = true
}

resource "yandex_cdn_origin_group" "media_storage" {
  name          = "findme-cdn-object-storage"
  folder_id     = var.folder_id
  provider_type = "ourcdn"
  use_next      = true

  origin {
    source  = "storage.yandexcloud.net"
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "yandex_cdn_origin_group" "media_originals" {
  name          = "findme-cdn-originals"
  folder_id     = var.folder_id
  provider_type = "ourcdn"
  use_next      = true

  origin {
    source  = "findme-photo.ru"
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "findmecdn_endpoint" "media" {
  folder_id              = var.folder_id
  cname                  = "img.findme-photo.ru"
  active                 = true
  provider_type          = "ourcdn"
  origin_group_id        = yandex_cdn_origin_group.media_originals.id
  origin_protocol        = "HTTPS"
  certificate_manager_id = "fpqkhstccrsfl899lq4h"
  tls_profile            = "PROFILE_COMPATIBLE"
  options_json = jsonencode({
    edgeCacheSettings    = { enabled = true, value = { simpleValue = "2592000" } }
    browserCacheSettings = { enabled = false }
    queryParamsOptions   = { ignoreQueryString = { enabled = true, value = true } }
    hostOptions          = { host = { enabled = true, value = "findme-photo.ru" } }
    allowedHttpMethods   = { enabled = true, value = ["GET", "HEAD"] }
    staticRequestHeaders = { enabled = false }
    customServerName     = { enabled = false }
    ignoreCookie         = { enabled = true, value = true }
    secureKey            = { enabled = false, type = "DISABLE_IP_SIGNING" }
  })

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  cdn_media_path = replace(var.media_delivery_bucket, ".", "\\.")
  cdn_image_cache = {
    edgeCacheSettings = { enabled = true, value = { customValues = { "200" = "2592000", "206" = "2592000" } } }
    queryParamsOptions = {
      ignoreQueryString = { enabled = true, value = true }
    }
    customServerName = { enabled = false }
  }
  cdn_storage_headers = {
    hostOptions          = { host = { enabled = true, value = "storage.yandexcloud.net" } }
    staticRequestHeaders = { enabled = false }
  }
  cdn_private_image = {
    secureKey     = { enabled = true, type = "DISABLE_IP_SIGNING" }
    staticHeaders = { enabled = true, value = { "Cache-Control" = "private, no-cache" } }
  }
}

resource "findmecdn_rule" "public_covers" {
  resource_id     = findmecdn_endpoint.media.id
  name            = "public-covers"
  pattern         = "^/${local.cdn_media_path}/event-covers/[a-f0-9-]{36}(\\.[a-z0-9]{1,10})?$"
  weight          = "10"
  origin_group_id = yandex_cdn_origin_group.media_storage.id
  origin_protocol = "HTTPS"
  options_json = jsonencode(merge(local.cdn_image_cache, local.cdn_storage_headers, {
    browserCacheSettings = { enabled = true, value = "31536000" }
    secureKey            = { enabled = false, type = "DISABLE_IP_SIGNING" }
  }))
  lifecycle {
    prevent_destroy = true
  }
}

resource "findmecdn_rule" "private_previews" {
  resource_id     = findmecdn_endpoint.media.id
  name            = "private-previews"
  pattern         = "^/${local.cdn_media_path}/previews/[\\w-]+/[\\w-]+/[a-f0-9-]+\\.jpg$"
  weight          = "20"
  origin_group_id = yandex_cdn_origin_group.media_storage.id
  origin_protocol = "HTTPS"
  options_json    = jsonencode(merge(local.cdn_image_cache, local.cdn_storage_headers, local.cdn_private_image))
  secure_key_wo   = var.secure_key

  lifecycle {
    prevent_destroy = true
  }
}

resource "findmecdn_rule" "private_originals" {
  resource_id     = findmecdn_endpoint.media.id
  name            = "private-originals"
  pattern         = "^/delivery/originals/[0-9a-f]{32}/(inline|findme-photo-[A-Za-z0-9_-]{1,32}\\.(jpg|png))$"
  weight          = "30"
  origin_group_id = yandex_cdn_origin_group.media_originals.id
  origin_protocol = "HTTPS"
  options_json = jsonencode(merge(local.cdn_image_cache, local.cdn_private_image, {
    hostOptions          = { host = { enabled = true, value = "findme-photo.ru" } }
    staticRequestHeaders = { enabled = true, value = { "X-FindMe-Origin-Auth" = "" } }
  }))
  secure_key_wo  = var.secure_key
  origin_auth_wo = var.origin_auth

  lifecycle {
    prevent_destroy = true
  }
}

resource "findmecdn_rule" "deny_other_paths" {
  resource_id = findmecdn_endpoint.media.id
  name        = "deny-other-paths"
  pattern     = "^/.*"
  weight      = "9999"
  options_json = jsonencode({
    customServerName = { enabled = false }
    staticResponse   = { enabled = true, code = "404", content = "Not Found" }
  })

  lifecycle {
    prevent_destroy = true
  }
}

# Retained CDN group from the former imgproxy origin. No route uses it.
resource "yandex_cdn_origin_group" "legacy_image_origin" {
  name          = "findme-gallery-image-origin"
  folder_id     = var.folder_id
  provider_type = "ourcdn"
  use_next      = true

  origin {
    source  = "img-origin.findme-photo.ru"
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}
