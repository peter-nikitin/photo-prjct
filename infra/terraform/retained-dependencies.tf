# Nonsecret inventory of retained objects with another lifecycle owner.
# These locals/output do not create cloud objects or read CDN secret-bearing APIs.
locals {
  monitoring_config = jsondecode(file("${path.module}/../../deploy/monitoring/prometheus/environment.json"))
  retained_cloud_dependencies = {
    cdn = {
      ownership          = "external-controller"
      id                 = "bc8ryczd5bpuzduhwubz"
      folder_id          = var.folder_id
      cname              = "img.findme-photo.ru"
      origin_group_id    = yandex_cdn_origin_group.gallery.id
      certificate_id     = yandex_cm_certificate.gallery.id
      config_sources     = ["deploy/image-origin/provision.py", "deploy/image-origin/configure-cdn.sh"]
      secret_entry_names = ["GALLERY_CDN_TOKEN_SECRET", "IMAGE_ORIGIN_HEADER_SECRET"]
      protected_reason   = "Image delivery remains required; native CDN Read exposes configured signing key and origin-auth headers in state."
    }
    monitoring = {
      ownership         = "external-controller"
      workspace_id      = local.monitoring_config.workspace_id
      dashboard_id      = local.monitoring_config.dashboard_id
      dashboard_source  = "deploy/monitoring/prometheus/single-vm-dashboard.json"
      controller_source = "deploy/monitoring/prometheus/single_vm_dashboard.py"
      workflow_source   = ".github/workflows/monitoring.yml"
      config_source     = "deploy/monitoring/prometheus/environment.json"
      api_confirmed     = "Dashboard Get confirmed this ID in the earlier audit; current metric freshness and workflow outcome are not proved here."
      protected_reason  = "Existing single VM controller owns the dashboard; no second Terraform writer."
    }
    legacy_monitoring_alerts = {
      ownership                            = "pending-review"
      observed_rule_file                   = "findme-photo.yml"
      observed_groups                      = 4
      observed_rules                       = 33
      formerly_configured_channels         = ["findme-photo-operator-email", "findme-photo-operator-telegram"]
      formerly_configured_email_channel_id = "cloud__b1gmcsmr51o5kvp86l55_findme-photo-operator-email"
      protected_reason                     = "Earlier API rule read and repository configuration; current dashboard-only controller no longer manages rules, routing or channels. Audit their live state before any removal."
    }
    backup_recovery = {
      ownership                   = "data-and-system-child"
      folder_id                   = var.folder_id
      old_registration_id         = "505d0bdd-a624-4b6d-b9d2-1d1ef78b984e"
      current_app_registration_id = "ce81900e-9714-450a-8361-e9c808dbe940"
      archive_id                  = "c45fbb19-3a7a-5452-0e6d-257c29f0952e"
      recovery_point_ids = [
        "a8a9af43-acff-4db2-9e2e-35ccb4be394a",
        "c22957b4-f7bf-450b-973d-759c4395b0a7",
        "cf25d3a4-0de8-44bc-bcd4-013bbea08849",
        "7633998c-c630-46f5-83cb-9533047da51b",
        "ae13835d-b1b5-4c4c-a49f-ab1f7bed64bf",
        "1d949b25-c7f2-4fbd-91dd-18c49be9a299",
        "5855ebe4-89f6-4d88-8d59-08f5c4a3f1f6",
        "9033962a-42da-4412-aa9d-686dff1f5b82",
        "64fdf856-7276-4e07-90df-b1b5d8177c46",
        "4a8ff0e7-2bba-434a-a681-d93171a5efd7",
        "dcc21776-f369-42f6-98be-f92b9720f179",
        "42efcf9b-122d-4812-bb7a-86dc6960f4c1",
        "05a653fa-e6d5-4436-ab6a-c63310cf684d",
        "b3077e7d-8471-408a-886c-90855cbd5bae",
        "f674f87d-d890-4e00-8e14-5dda6dc9a397",
      ]
      protected_reason = "Retain historical archive and recovery points until independent restore proof; deleted source VM does not retire backup data."
    }
    network_children = {
      ownership                  = "data-and-system-child"
      default_security_group_ids = ["enpclrep8uilre076c6q", "enpvnto5k0jdjjvmad5b"]
      private_dns_zone_ids       = ["dns3n0ajvssu5kefthuv", "dns3ph5gu24j63jpr5on", "dns96hl2kics29i7f0u0", "dns9modo2npc587d3j8t"]
      peer_security_group_id     = "enp2dpjqa3i8cttdsnpp"
      protected_reason           = "Network-generated children and current image-origin peer reference; review SG rules before retiring peer."
    }
    pending_audit = {
      ownership                            = "pending-review"
      legacy_monitoring_service_account_id = yandex_iam_service_account.legacy_monitoring.id
      current_additional_folder_roles      = ["vpc.viewer", "backup.user"]
      cloud_logging                        = "Group list returned Unavailable; absence not confirmed."
      protected_reason                     = "Audit existing callers, API keys, and cross-folder permissions before removal."
    }
  }
}
output "retained_cloud_dependencies" {
  description = "Nonsecret reviewed inventory of retained external/controller/data dependencies; not managed resources."
  value       = local.retained_cloud_dependencies
}
