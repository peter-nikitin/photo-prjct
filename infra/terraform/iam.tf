resource "yandex_iam_service_account" "backup_agent" {
  name      = "findme-photo-backup-agent"
  folder_id = var.folder_id
}

resource "yandex_resourcemanager_folder_iam_member" "backup_agent" {
  folder_id = var.folder_id
  role      = "backup.user"
  member    = "serviceAccount:${yandex_iam_service_account.backup_agent.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "infrastructure_lb_editor" {
  folder_id = var.folder_id
  role      = "load-balancer.editor"
  member    = "serviceAccount:${var.infrastructure_service_account_id}"
}
