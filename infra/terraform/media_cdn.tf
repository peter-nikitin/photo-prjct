module "media_cdn" {
  source = "./modules/media-cdn"

  folder_id             = var.folder_id
  media_delivery_bucket = yandex_storage_bucket.media_delivery.bucket
  secure_key            = var.cdn_secure_key
  origin_auth           = var.cdn_origin_auth
}

import {
  for_each = var.import_existing_cdn ? toset(["1534077880908801403"]) : toset([])
  to       = module.media_cdn.yandex_cdn_origin_group.media_storage
  id       = each.value
}

import {
  for_each = var.import_existing_cdn ? toset(["5596555671617786856"]) : toset([])
  to       = module.media_cdn.yandex_cdn_origin_group.media_originals
  id       = each.value
}

import {
  for_each = var.import_existing_cdn ? toset(["bc8ryczd5bpuzduhwubz"]) : toset([])
  to       = module.media_cdn.findmecdn_endpoint.media
  id       = each.value
}

import {
  for_each = var.import_existing_cdn ? toset(["bc8ryczd5bpuzduhwubz/65840"]) : toset([])
  to       = module.media_cdn.findmecdn_rule.public_covers
  id       = each.value
}

import {
  for_each = var.import_existing_cdn ? toset(["bc8ryczd5bpuzduhwubz/65843"]) : toset([])
  to       = module.media_cdn.findmecdn_rule.private_previews
  id       = each.value
}

import {
  for_each = var.import_existing_cdn ? toset(["bc8ryczd5bpuzduhwubz/65847"]) : toset([])
  to       = module.media_cdn.findmecdn_rule.private_originals
  id       = each.value
}

import {
  for_each = var.import_existing_cdn ? toset(["bc8ryczd5bpuzduhwubz/65835"]) : toset([])
  to       = module.media_cdn.findmecdn_rule.deny_other_paths
  id       = each.value
}

import {
  for_each = var.import_existing_cdn ? toset(["2569228330826683012"]) : toset([])
  to       = module.media_cdn.yandex_cdn_origin_group.legacy_image_origin
  id       = each.value
}

variable "cdn_secure_key" {
  type      = string
  default   = null
  ephemeral = true
  sensitive = true
}

variable "cdn_origin_auth" {
  type      = string
  default   = null
  ephemeral = true
  sensitive = true
}

variable "import_existing_cdn" {
  description = "Adopt the live media CDN. Offline tests use a disposable state and disable imports."
  type        = bool
  default     = true
}
