# Existing daily policy and binding retain recovery lineage; the source VM is absent.
resource "yandex_backup_policy" "legacy_daily" {
  name                              = "Default daily"
  folder_id                         = var.folder_id
  compression                       = "NORMAL"
  format                            = "AUTO"
  multi_volume_snapshotting_enabled = true
  silent_mode_enabled               = true
  splitting_bytes                   = "1099511627776"
  vss_provider                      = "TARGET_SYSTEM_DEFINED"
  archive_name                      = "[Machine Name]-[Plan ID]-[Unique ID]A"
  cbt                               = "ENABLE_AND_USE"
  fast_backup_enabled               = true
  reattempts {
    enabled      = true
    interval     = "30s"
    max_attempts = 30
  }
  vm_snapshot_reattempts {
    enabled      = true
    interval     = "5m"
    max_attempts = 3
  }
  retention {
    rules { max_count = 15 }
  }
  scheduling {
    enabled           = true
    random_max_delay  = "30m"
    scheme            = "ALWAYS_INCREMENTAL"
    weekly_backup_day = "MONDAY"
    backup_sets {
      type = "TYPE_AUTO"
      execute_by_time {
        type      = "DAILY"
        repeat_at = ["01:45"]
      }
    }
  }
  lifecycle { prevent_destroy = true }
}
resource "yandex_backup_policy_bindings" "legacy_daily" {
  policy_id   = yandex_backup_policy.legacy_daily.id
  instance_id = "epdr5g3p24tdns9890nr"
  lifecycle { prevent_destroy = true }
}
