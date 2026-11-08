resource "yandex_lockbox_secret" "deployment" {
  name                = "findme-photo"
  folder_id           = var.folder_id
  description         = "Authoritative secret set for FindMe Photo deployment"
  labels              = { env = "staging", managed-by = "yc-cli", project = "findme-photo" }
  deletion_protection = true
  lifecycle { prevent_destroy = true }
}
resource "yandex_lockbox_secret" "vm_deploy" {
  name                = "findme-photo-vm-deploy"
  folder_id           = var.folder_id
  description         = "Dedicated VM deploy SSH identity; bootstrap 2026-11-07"
  labels              = { project = "findme-photo", purpose = "vm-ssh-deploy" }
  deletion_protection = true
  lifecycle { prevent_destroy = true }
}
resource "yandex_lockbox_secret" "vm_admin" {
  name                = "findme-photo-vm-admin"
  folder_id           = var.folder_id
  description         = "Dedicated VM admin SSH identity; bootstrap 2026-11-07"
  labels              = { project = "findme-photo", purpose = "vm-ssh-admin" }
  deletion_protection = true
  lifecycle { prevent_destroy = true }
}
locals {
  lockbox_members = {
    deploy_ci_view          = { secret_id = yandex_lockbox_secret.deployment.id, role = "lockbox.viewer", account_id = yandex_iam_service_account.lockbox_ci.id }
    deploy_ci_payload       = { secret_id = yandex_lockbox_secret.deployment.id, role = "lockbox.payloadViewer", account_id = yandex_iam_service_account.lockbox_ci.id }
    vm_deploy_ci_view       = { secret_id = yandex_lockbox_secret.vm_deploy.id, role = "lockbox.viewer", account_id = yandex_iam_service_account.lockbox_ci.id }
    vm_deploy_ci_payload    = { secret_id = yandex_lockbox_secret.vm_deploy.id, role = "lockbox.payloadViewer", account_id = yandex_iam_service_account.lockbox_ci.id }
    vm_deploy_infra_view    = { secret_id = yandex_lockbox_secret.vm_deploy.id, role = "lockbox.viewer", account_id = yandex_iam_service_account.infrastructure.id }
    vm_deploy_infra_payload = { secret_id = yandex_lockbox_secret.vm_deploy.id, role = "lockbox.payloadViewer", account_id = yandex_iam_service_account.infrastructure.id }
  }
}
resource "yandex_lockbox_secret_iam_member" "adopted" {
  for_each  = local.lockbox_members
  secret_id = each.value.secret_id
  role      = each.value.role
  member    = "serviceAccount:${each.value.account_id}"
}
