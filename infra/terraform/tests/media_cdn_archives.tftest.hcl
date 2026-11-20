mock_provider "yandex" {
  override_during = plan
}

mock_provider "findmecdn" {
  override_during = plan
}

run "route_only_signed_archives_to_scratch" {
  command = plan

  module {
    source = "./modules/media-cdn"
  }

  override_resource {
    target          = findmecdn_rule.private_previews
    override_during = plan
    values          = { id = "65843" }
  }

  override_resource {
    target          = yandex_cdn_origin_group.media_storage
    override_during = plan
    values          = { id = "1534077880908801403" }
  }

  variables {
    folder_id             = "b1g2qttgfhb4gdunvlge"
    media_delivery_bucket = "findme-photo-media-delivery-prod-b1g2qttg"
    media_scratch_bucket  = "findme-photo-media-scratch-prod-b1g2qttg"
  }

  assert {
    condition = (
      can(regex(findmecdn_rule.private_archives.pattern, "/${var.media_scratch_bucket}/archives/12345678-1234-1234-1234-123456789abc.zip")) &&
      !can(regex(findmecdn_rule.private_archives.pattern, "/${var.media_scratch_bucket}/uploads/12345678-1234-1234-1234-123456789abc.zip")) &&
      !can(regex(findmecdn_rule.private_archives.pattern, "/${var.media_scratch_bucket}/archives/12345678-1234-1234-1234-123456789abc.zip/extra")) &&
      tonumber(findmecdn_rule.private_archives.weight) < tonumber(findmecdn_rule.deny_other_paths.weight)
    )
    error_message = "Only UUID ZIP keys may match the archive rule before the catch-all."
  }

  assert {
    condition = (
      findmecdn_rule.private_archives.secure_key_source_rule_id == findmecdn_rule.private_previews.id &&
      findmecdn_rule.private_archives.origin_group_id == yandex_cdn_origin_group.media_storage.id &&
      findmecdn_rule.private_archives.origin_protocol == "HTTPS" &&
      jsondecode(findmecdn_rule.private_archives.options_json).secureKey.enabled &&
      jsondecode(findmecdn_rule.private_archives.options_json).queryParamsOptions.ignoreQueryString.value &&
      jsondecode(findmecdn_rule.private_archives.options_json).slice.value &&
      jsondecode(findmecdn_rule.private_archives.options_json).edgeCacheSettings.value.customValues["200"] == "600" &&
      jsondecode(findmecdn_rule.private_archives.options_json).edgeCacheSettings.value.customValues["206"] == "600" &&
      contains(jsondecode(findmecdn_rule.private_archives.options_json).cacheHttpHeaders.value, "Content-Disposition") &&
      jsondecode(findmecdn_rule.private_archives.options_json).staticHeaders.value["Cache-Control"] == "private, no-store"
    )
    error_message = "The ZIP route uses the existing signing key and caches attachment ranges at the edge."
  }
}
