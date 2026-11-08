resource "yandex_iam_service_account" "app" {
  name      = "find-me-staging"
  folder_id = var.folder_id
}
resource "yandex_iam_service_account" "hires" {
  name        = "sa-hires-staging"
  folder_id   = var.folder_id
  description = "Service account for hires-staging bucket"
}
resource "yandex_iam_service_account" "selfie_feedback" {
  name        = "sa-selfie-feedback-staging"
  folder_id   = var.folder_id
  description = "Web-only service account for consented selfie-search feedback objects"
}
resource "yandex_iam_service_account" "lockbox_ci" {
  name        = "sa-lockbox-ci-staging"
  folder_id   = var.folder_id
  description = "GitHub Actions reader for FindMe staging Lockbox"
  labels      = { env = "staging", managed-by = "yc-cli", project = "findme-photo" }
}
resource "yandex_iam_service_account" "tfstate_backend" {
  name      = "findme-photo-tfstate-backend"
  folder_id = var.folder_id
  lifecycle { prevent_destroy = true }
}
resource "yandex_iam_service_account" "infrastructure" {
  name      = "findme-photo-infrastructure"
  folder_id = var.folder_id
  lifecycle { prevent_destroy = true }
}

locals {
  adopted_folder_roles = {
    app_storage           = { role = "storage.editor", service_account_id = yandex_iam_service_account.app.id }
    infra_resource        = { role = "resource-manager.admin", service_account_id = yandex_iam_service_account.infrastructure.id }
    infra_compute         = { role = "compute.editor", service_account_id = yandex_iam_service_account.infrastructure.id }
    infra_iam             = { role = "iam.serviceAccounts.admin", service_account_id = yandex_iam_service_account.infrastructure.id }
    infra_public_vpc      = { role = "vpc.publicAdmin", service_account_id = yandex_iam_service_account.infrastructure.id }
    infra_private_vpc     = { role = "vpc.privateAdmin", service_account_id = yandex_iam_service_account.infrastructure.id }
    infra_security_groups = { role = "vpc.securityGroups.admin", service_account_id = yandex_iam_service_account.infrastructure.id }
  }
}
resource "yandex_resourcemanager_folder_iam_member" "adopted" {
  for_each  = local.adopted_folder_roles
  folder_id = var.folder_id
  role      = each.value.role
  member    = "serviceAccount:${each.value.service_account_id}"
}
