variable "media_storage_principals" {
  type = object({
    private_media = string
    cover_writer  = string
    image_origin  = string
  })
  description = "Canonical Yandex service account IDs for private media, cover uploads and image origin."
  validation {
    condition = (
      alltrue([for id in values(var.media_storage_principals) : can(regex("^[a-z0-9]{20}$", id))]) &&
      var.media_storage_principals.image_origin != var.media_storage_principals.private_media &&
      var.media_storage_principals.image_origin != var.media_storage_principals.cover_writer
    )
    error_message = "Provide service account IDs and keep image origin separate from both application writers."
  }
}

variable "media_upload_origins" {
  type        = set(string)
  description = "Exact HTTPS browser origins allowed to POST photos to the scratch bucket."
  validation {
    condition = (
      length(var.media_upload_origins) > 0 &&
      alltrue([for origin in var.media_upload_origins : can(regex("^https://[a-zA-Z0-9.-]+(:[0-9]+)?$", origin))])
    )
    error_message = "Provide explicit HTTPS origins without paths or wildcards."
  }
}

locals {
  media_bucket_suffix = substr(var.folder_id, 0, 8)

  # Provider management and refresh also use S3 bucket-level operations.
  media_storage_admin = trimprefix(yandex_resourcemanager_folder_iam_member.infrastructure_storage.member, "serviceAccount:")
}

resource "yandex_storage_bucket" "media_originals" {
  bucket                = "findme-photo-media-originals-prod-${local.media_bucket_suffix}"
  folder_id             = var.folder_id
  default_storage_class = "COLD"
  force_destroy         = false

  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [yandex_resourcemanager_folder_iam_member.infrastructure_storage]
}

resource "yandex_storage_bucket" "media_delivery" {
  bucket                = "findme-photo-media-delivery-prod-${local.media_bucket_suffix}"
  folder_id             = var.folder_id
  default_storage_class = "STANDARD"
  force_destroy         = false

  # Exact cover reads are granted by their object ACLs.
  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [yandex_resourcemanager_folder_iam_member.infrastructure_storage]
}

resource "yandex_storage_bucket" "media_scratch" {
  bucket                = "findme-photo-media-scratch-prod-${local.media_bucket_suffix}"
  folder_id             = var.folder_id
  default_storage_class = "STANDARD"
  force_destroy         = false

  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }

  cors_rule {
    allowed_headers = ["content-type"]
    allowed_methods = ["POST"]
    allowed_origins = var.media_upload_origins
  }

  # Leave versioning unset to retain the unversioned state of a new bucket.
  lifecycle_rule {
    id      = "expire-preview-jobs-after-7-days"
    enabled = true
    filter {
      prefix = "preview-jobs/"
    }
    expiration {
      days = 7
    }
  }

  lifecycle_rule {
    id      = "selfie-search-expire-after-24h"
    enabled = true
    filter {
      prefix = "selfie-search/"
    }
    expiration {
      days = 1
    }
  }

  depends_on = [yandex_resourcemanager_folder_iam_member.infrastructure_storage]
}

# A request first needs IAM or ACL authorization on closed buckets.
# Bucket policies below narrow object operations to each principal's own prefixes.
resource "yandex_storage_bucket_iam_binding" "media_originals_writer" {
  bucket  = yandex_storage_bucket.media_originals.bucket
  role    = "storage.editor"
  members = ["serviceAccount:${var.media_storage_principals.private_media}"]
}

resource "yandex_storage_bucket_iam_binding" "media_delivery_writers" {
  bucket = yandex_storage_bucket.media_delivery.bucket
  role   = "storage.uploader"
  members = toset([
    "serviceAccount:${var.media_storage_principals.private_media}",
    "serviceAccount:${var.media_storage_principals.cover_writer}"
  ])
}

resource "yandex_storage_bucket_iam_binding" "media_delivery_origin" {
  bucket  = yandex_storage_bucket.media_delivery.bucket
  role    = "storage.viewer"
  members = ["serviceAccount:${var.media_storage_principals.image_origin}"]
}

resource "yandex_storage_bucket_iam_binding" "media_scratch_writer" {
  bucket  = yandex_storage_bucket.media_scratch.bucket
  role    = "storage.editor"
  members = ["serviceAccount:${var.media_storage_principals.private_media}"]
}

resource "yandex_storage_bucket_policy" "media_originals" {
  bucket = yandex_storage_bucket.media_originals.bucket
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = local.media_storage_admin }
        Action    = ["s3:*"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_originals.bucket}"
      },
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = var.media_storage_principals.private_media }
        Action    = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_originals.bucket}/originals/*"
      },
      {
        # An exact-key HEAD for a missing object must return 404 rather than 403.
        Effect    = "Allow"
        Principal = { CanonicalUser = var.media_storage_principals.private_media }
        Action    = ["s3:ListBucket"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_originals.bucket}"
      }
    ]
  })
}

resource "yandex_storage_bucket_policy" "media_delivery" {
  bucket = yandex_storage_bucket.media_delivery.bucket
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = local.media_storage_admin }
        Action    = ["s3:*"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_delivery.bucket}"
      },
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = var.media_storage_principals.private_media }
        Action    = ["s3:GetObject", "s3:PutObject"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_delivery.bucket}/previews/*"
      },
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = var.media_storage_principals.cover_writer }
        Action    = ["s3:GetObject", "s3:PutObject", "s3:PutObjectAcl"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_delivery.bucket}/event-covers/*"
      },
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = distinct([var.media_storage_principals.private_media, var.media_storage_principals.cover_writer]) }
        Action    = ["s3:ListBucket"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_delivery.bucket}"
      },
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = var.media_storage_principals.image_origin }
        Action    = ["s3:GetObject"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_delivery.bucket}/previews/*"
      }
    ]
  })
}

resource "yandex_storage_bucket_policy" "media_scratch" {
  bucket = yandex_storage_bucket.media_scratch.bucket
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = local.media_storage_admin }
        Action    = ["s3:*"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_scratch.bucket}"
      },
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = var.media_storage_principals.private_media }
        Action    = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "arn:aws:s3:::${yandex_storage_bucket.media_scratch.bucket}/uploads/*",
          "arn:aws:s3:::${yandex_storage_bucket.media_scratch.bucket}/preview-jobs/*",
          "arn:aws:s3:::${yandex_storage_bucket.media_scratch.bucket}/selfie-search/*"
        ]
      },
      {
        Effect    = "Allow"
        Principal = { CanonicalUser = var.media_storage_principals.private_media }
        Action    = ["s3:ListBucket", "s3:GetBucketVersioning", "s3:GetLifecycleConfiguration"]
        Resource  = "arn:aws:s3:::${yandex_storage_bucket.media_scratch.bucket}"
      }
    ]
  })
}

output "media_storage_buckets" {
  description = "Bucket names for the three ADR 0068 application storage roles."
  value = {
    originals = yandex_storage_bucket.media_originals.bucket
    delivery  = yandex_storage_bucket.media_delivery.bucket
    scratch   = yandex_storage_bucket.media_scratch.bucket
  }
}
