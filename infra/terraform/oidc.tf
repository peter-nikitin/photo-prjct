resource "yandex_iam_workload_identity_oidc_federation" "deployment" {
  name        = "github-findme-staging"
  folder_id   = var.folder_id
  description = "GitHub Actions OIDC for FindMe staging"
  issuer      = "https://token.actions.githubusercontent.com"
  jwks_url    = "https://token.actions.githubusercontent.com/.well-known/jwks"
  audiences   = ["https://github.com/peter-nikitin"]
  labels      = { env = "staging", managed-by = "yc-cli", project = "findme-photo" }
  lifecycle { prevent_destroy = true }
}
resource "yandex_iam_workload_identity_oidc_federation" "infrastructure" {
  name      = "findme-photo-infrastructure"
  folder_id = var.folder_id
  issuer    = "https://token.actions.githubusercontent.com"
  jwks_url  = "https://token.actions.githubusercontent.com/.well-known/jwks"
  audiences = ["https://github.com/peter-nikitin"]
  lifecycle { prevent_destroy = true }
}
resource "yandex_iam_workload_identity_federated_credential" "deployment" {
  service_account_id  = yandex_iam_service_account.lockbox_ci.id
  federation_id       = yandex_iam_workload_identity_oidc_federation.deployment.id
  external_subject_id = "repo:peter-nikitin/photo-prjct:ref:refs/heads/main"
}
resource "yandex_iam_workload_identity_federated_credential" "infrastructure" {
  service_account_id  = yandex_iam_service_account.infrastructure.id
  federation_id       = yandex_iam_workload_identity_oidc_federation.infrastructure.id
  external_subject_id = "repo:peter-nikitin/photo-prjct:environment:infrastructure"
}
