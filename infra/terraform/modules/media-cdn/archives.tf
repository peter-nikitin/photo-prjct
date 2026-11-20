variable "media_scratch_bucket" {
  type = string
}

locals {
  cdn_archive_pattern = "^/${replace(var.media_scratch_bucket, ".", "\\.")}/archives/[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}\\.zip$"
}

resource "findmecdn_rule" "private_archives" {
  resource_id               = findmecdn_endpoint.media.id
  name                      = "private-archives"
  pattern                   = local.cdn_archive_pattern
  weight                    = "40"
  origin_group_id           = yandex_cdn_origin_group.media_storage.id
  origin_protocol           = "HTTPS"
  secure_key_source_rule_id = findmecdn_rule.private_previews.id
  secure_key_wo             = var.secure_key

  options_json = jsonencode(merge(local.cdn_image_cache, local.cdn_storage_headers, local.cdn_private_image, {
    edgeCacheSettings = { enabled = true, value = { customValues = { "200" = "600", "206" = "600" } } }
    slice             = { enabled = true, value = true }
    staticHeaders     = { enabled = true, value = { "Cache-Control" = "private, no-store" } }
  }))

  lifecycle {
    precondition {
      condition     = length(local.cdn_archive_pattern) <= 100
      error_message = "The archive CDN route exceeds the Yandex Cloud rule pattern limit."
    }
  }
}
