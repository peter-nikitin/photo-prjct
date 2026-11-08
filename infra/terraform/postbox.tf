resource "yandex_iam_service_account" "postbox_sender" {
  name        = "sa-postbox-sender"
  folder_id   = var.folder_id
  description = "FindMe Photo transactional email sender via Yandex Cloud Postbox"
  lifecycle { prevent_destroy = true }
}

resource "yandex_resourcemanager_folder_iam_member" "postbox_sender" {
  folder_id = var.folder_id
  role      = "postbox.sender"
  member    = "serviceAccount:${yandex_iam_service_account.postbox_sender.id}"
}

# Domain from configured sender; identity metadata and import readiness are not yet verified.
resource "aws_sesv2_email_identity" "sender" {
  provider       = aws.postbox
  email_identity = "findme-photo.ru"
  lifecycle { prevent_destroy = true }
}
