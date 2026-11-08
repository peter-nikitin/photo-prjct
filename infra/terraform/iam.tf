resource "yandex_iam_service_account" "backup_agent" {
  name      = "findme-photo-backup-agent"
  folder_id = var.folder_id
}

resource "yandex_resourcemanager_folder_iam_member" "backup_agent" {
  folder_id = var.folder_id
  role      = "backup.user"
  member    = "serviceAccount:${yandex_iam_service_account.backup_agent.id}"
}

# Grant is included in the same reviewed infrastructure plan; no live grant
# is made until its specific recipient and role are approved.
resource "yandex_resourcemanager_folder_iam_member" "infrastructure_nlb" {
  folder_id = var.folder_id
  role      = "load-balancer.editor"
  member    = "serviceAccount:ajema849qjho2lgjaqvo"
}

# The attached VM identity sends only metrics to the existing workspace.
resource "yandex_resourcemanager_folder_iam_member" "monitoring_writer" {
  folder_id = var.folder_id
  role      = "monitoring.editor"
  member    = "serviceAccount:${yandex_iam_service_account.backup_agent.id}"
}
