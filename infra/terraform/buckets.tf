# Existing application buckets. Future plan must accept imports without other changes.
resource "yandex_storage_bucket" "private_media" {
  bucket                = "hires-staging"
  folder_id             = var.folder_id
  default_storage_class = "STANDARD"
  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }
  versioning { enabled = false }
  cors_rule {
    allowed_headers = ["Content-Type"]
    allowed_methods = ["POST"]
    allowed_origins = ["http://localhost:8000", "https://findme-photo.ru"]
    max_age_seconds = 3600
  }
  lifecycle_rule {
    id      = "expire-preview-staging-after-7-days"
    enabled = true
    filter { prefix = "processing-pending/previews/" }
    expiration { days = 7 }
  }
  lifecycle_rule {
    id      = "selfie-search-expire-after-24h"
    enabled = true
    filter { prefix = "selfie-search/" }
    expiration { days = 1 }
  }
  lifecycle { prevent_destroy = true }
}
resource "yandex_storage_bucket" "public_media" {
  bucket                = "findme-photo-public-media-b1g2qttg"
  folder_id             = var.folder_id
  default_storage_class = "STANDARD"
  max_size              = 107374182400
  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }
  versioning { enabled = true }
  lifecycle { prevent_destroy = true }
}
resource "yandex_storage_bucket" "selfie_feedback" {
  bucket                = "findme-selfie-feedback-staging-b1g2qttg"
  folder_id             = var.folder_id
  default_storage_class = "STANDARD"
  max_size              = 53687091200
  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }
  versioning { enabled = false }
  lifecycle_rule {
    id      = "selfie-feedback-expire-after-30d"
    enabled = true
    expiration { days = 30 }
  }
  server_side_encryption_configuration {
    rule {
      apply_server_side_encryption_by_default {
        kms_master_key_id = yandex_kms_symmetric_key.selfie_feedback.id
        sse_algorithm     = "aws:kms"
      }
    }
  }
  lifecycle { prevent_destroy = true }
}
# Partial management: current credentials cannot read full bucket configuration.
# These exact nested fields are unverified; import and a zero-diff plan require full read.
resource "yandex_storage_bucket" "tfstate" {
  bucket                = "findme-photo-tfstate"
  folder_id             = var.folder_id
  default_storage_class = "STANDARD"
  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }
  versioning { enabled = true }
  lifecycle {
    prevent_destroy = true
    ignore_changes = [policy, acl, grant, cors_rule, lifecycle_rule,
      server_side_encryption_configuration, tags, website, logging,
    object_lock_configuration]
  }
}

resource "yandex_storage_bucket_policy" "private_media" {
  bucket = yandex_storage_bucket.private_media.bucket
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "AllowFindMeGalleryImageOriginPreviewReads", Effect = "Allow", Principal = { CanonicalUser = yandex_iam_service_account.image_origin.id }, Action = "s3:GetObject", Resource = "arn:aws:s3:::hires-staging/derivatives/previews/*" },
      { Sid = "AllowFindMeApplicationPrivateMediaAccess", Effect = "Allow", Principal = "*", Action = "s3:*", Resource = ["arn:aws:s3:::hires-staging", "arn:aws:s3:::hires-staging/*"], Condition = { StringEquals = { "yc:access-key-id" = "YCAJE4ZLwJYt4cqvnZFtTvm2z" } } }
    ]
  })
}

resource "yandex_storage_bucket_grant" "private_media" {
  bucket = yandex_storage_bucket.private_media.bucket
  grant {
    id          = yandex_iam_service_account.hires.id
    type        = "CanonicalUser"
    permissions = ["READ", "WRITE"]
  }
}
resource "yandex_storage_bucket_grant" "public_media" {
  bucket = yandex_storage_bucket.public_media.bucket
  grant {
    id          = yandex_iam_service_account.app.id
    type        = "CanonicalUser"
    permissions = ["READ", "WRITE"]
  }
  grant {
    id          = "group:organization:bpf5t03kot36lj32f8il:users"
    type        = "CanonicalUser"
    permissions = ["FULL_CONTROL"]
  }
}
resource "yandex_storage_bucket_grant" "selfie_feedback" {
  bucket = yandex_storage_bucket.selfie_feedback.bucket
  grant {
    id          = yandex_iam_service_account.selfie_feedback.id
    type        = "CanonicalUser"
    permissions = ["FULL_CONTROL"]
  }
}
