resource "yandex_kms_symmetric_key" "selfie_feedback" {
  name                = "selfie-feedback-staging"
  folder_id           = var.folder_id
  description         = "KMS key for consented selfie-search feedback selfies"
  labels              = { env = "staging", managed-by = "yc-cli", project = "findme-photo" }
  default_algorithm   = "AES_256"
  rotation_period     = "31536000s"
  deletion_protection = true
  lifecycle { prevent_destroy = true }
}
resource "yandex_kms_symmetric_key_iam_member" "selfie_feedback" {
  symmetric_key_id = yandex_kms_symmetric_key.selfie_feedback.id
  role             = "kms.keys.encrypterDecrypter"
  member           = "serviceAccount:${yandex_iam_service_account.selfie_feedback.id}"
}
