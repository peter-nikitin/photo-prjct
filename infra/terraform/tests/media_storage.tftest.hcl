mock_provider "yandex" {}

override_resource {
  target = yandex_vpc_address.edge
  values = { external_ipv4_address = { address = "111.88.151.64" } }
}

run "isolate_media_buckets" {
  command = plan

  variables {
    cloud_id             = "b1gmcsmr51o5kvp86l55"
    folder_id            = "b1g2qttgfhb4gdunvlge"
    zone                 = "ru-central1-d"
    ubuntu_image_id      = "fd8p50cvjmb6fo0ik4hk"
    ssh_public_key       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN test"
    admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN admin"
    ssh_cidrs            = ["203.0.113.8/32"]
    boot_disk_gb         = 30
    postgres_disk_gb     = 10
    media_storage_principals = {
      private_media = "aje00000000000000001"
      cover_writer  = "aje00000000000000002"
      image_origin  = "aje00000000000000003"
    }
    media_upload_origins = ["https://findme-photo.ru"]
  }

  assert {
    condition = (
      yandex_storage_bucket.media_originals.default_storage_class == "COLD" &&
      yandex_storage_bucket.media_delivery.default_storage_class == "STANDARD" &&
      yandex_storage_bucket.media_scratch.default_storage_class == "STANDARD" &&
      length(yandex_storage_bucket.media_originals.lifecycle_rule) == 0 &&
      length(yandex_storage_bucket.media_delivery.lifecycle_rule) == 0
    )
    error_message = "Only the originals default to COLD, and durable objects have no expiration."
  }

  assert {
    condition = (
      { for rule in yandex_storage_bucket.media_scratch.lifecycle_rule : one(rule.filter).prefix => one(rule.expiration).days if rule.enabled } == {
        "archives/"      = 1
        "preview-jobs/"  = 7
        "selfie-search/" = 1
      } &&
      one([
        for rule in yandex_storage_bucket.media_scratch.lifecycle_rule :
        rule.abort_incomplete_multipart_upload_days if one(rule.filter).prefix == "archives/"
      ]) == 1 &&
      toset(one(yandex_storage_bucket.media_scratch.cors_rule).allowed_methods) == toset(["POST"]) &&
      toset(one(yandex_storage_bucket.media_scratch.cors_rule).allowed_origins) == var.media_upload_origins
    )
    error_message = "Temporary previews, selfies, and archives expire. Abandoned archive multipart uploads are cleaned up, and upload CORS stays restricted."
  }

  assert {
    condition = anytrue([
      for statement in jsondecode(yandex_storage_bucket_policy.media_scratch.policy).Statement :
      try(
        statement.Effect == "Deny" && statement.Principal == "*" &&
        toset(statement.Action) == toset(["s3:GetObject"]) &&
        statement.Resource == "arn:aws:s3:::${output.media_storage_buckets.scratch}/archives/*" &&
        length(statement.Condition.NotIpAddress["yc:OriginIp"]) > 0,
        false
      )
    ])
    error_message = "Archive reads from outside the CDN networks must be denied without opening other scratch objects."
  }

  assert {
    condition = (
      alltrue([for bucket in [
        yandex_storage_bucket.media_originals,
        yandex_storage_bucket.media_delivery,
        yandex_storage_bucket.media_scratch,
        ] : !bucket.force_destroy && length(bucket.anonymous_access_flags) == 1 && alltrue([
          for flags in bucket.anonymous_access_flags : !flags.read && !flags.list && !flags.config_read
      ])]) &&
      alltrue([for statement in jsondecode(yandex_storage_bucket_policy.media_delivery.policy).Statement :
        statement.Principal.CanonicalUser != var.media_storage_principals.image_origin ||
        (toset(statement.Action) == toset(["s3:GetObject"]) &&
        statement.Resource == "arn:aws:s3:::${output.media_storage_buckets.delivery}/previews/*")
      ]) &&
      length([for statement in jsondecode(yandex_storage_bucket_policy.media_delivery.policy).Statement :
        statement if statement.Principal.CanonicalUser == var.media_storage_principals.image_origin
      ]) == 1
    )
    error_message = "Buckets have no anonymous bucket access, and the CDN can read only ready previews."
  }

  assert {
    condition = (
      yandex_storage_bucket_iam_binding.media_delivery_origin.bucket == output.media_storage_buckets.delivery &&
      yandex_storage_bucket_iam_binding.media_delivery_origin.role == "storage.viewer" &&
      yandex_storage_bucket_iam_binding.media_delivery_origin.members == toset(["serviceAccount:${var.media_storage_principals.image_origin}"]) &&
      var.media_storage_principals.image_origin != trimprefix(yandex_resourcemanager_folder_iam_member.infrastructure_storage.member, "serviceAccount:")
    )
    error_message = "Image origin reaches the prefix policy through read-only IAM on delivery only."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(yandex_storage_bucket_policy.media_delivery.policy).Statement :
      !contains(statement.Action, "s3:PutObjectAcl") || (
        statement.Principal.CanonicalUser == var.media_storage_principals.cover_writer &&
        statement.Resource == "arn:aws:s3:::${output.media_storage_buckets.delivery}/event-covers/*"
      )
    ])
    error_message = "Only the cover writer can set object ACLs, and only in the cover prefix."
  }
}
