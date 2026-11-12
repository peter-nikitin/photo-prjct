resource "yandex_iam_service_account" "monitoring_ci" {
  name        = "findme-monitoring-ci"
  folder_id   = var.folder_id
  description = "GitHub OIDC for FindMe monitoring configuration"
  lifecycle { prevent_destroy = true }
}
resource "yandex_resourcemanager_folder_iam_member" "monitoring_ci" {
  folder_id = var.folder_id
  role      = "monitoring.editor"
  member    = "serviceAccount:${yandex_iam_service_account.monitoring_ci.id}"
}
resource "yandex_iam_workload_identity_federated_credential" "monitoring_ci" {
  service_account_id  = yandex_iam_service_account.monitoring_ci.id
  federation_id       = yandex_iam_workload_identity_oidc_federation.deployment.id
  external_subject_id = "repo:peter-nikitin/photo-prjct:environment:monitoring"
}
resource "yandex_iam_service_account" "image_origin" {
  name      = "findme-gallery-image-origin"
  folder_id = var.folder_id
  lifecycle { prevent_destroy = true }
}
resource "yandex_resourcemanager_folder_iam_member" "image_origin_storage" {
  folder_id = var.folder_id
  role      = "storage.viewer"
  member    = "serviceAccount:${yandex_iam_service_account.image_origin.id}"
}
resource "yandex_resourcemanager_folder_iam_member" "image_origin_monitoring" {
  folder_id = var.folder_id
  role      = "monitoring.editor"
  member    = "serviceAccount:${yandex_iam_service_account.image_origin.id}"
}
# Preserve until caller and API-key audit; other current roles are catalogued, not adopted.
resource "yandex_iam_service_account" "legacy_monitoring" {
  name        = "sa-dev-photo-monitoring"
  folder_id   = var.folder_id
  description = "Service account for Yandex Unified Agent on dev-photo-prjct VM"
  lifecycle { prevent_destroy = true }
}
resource "yandex_resourcemanager_folder_iam_member" "legacy_monitoring" {
  folder_id = var.folder_id
  role      = "monitoring.editor"
  member    = "serviceAccount:${yandex_iam_service_account.legacy_monitoring.id}"
}
