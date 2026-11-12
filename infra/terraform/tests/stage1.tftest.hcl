mock_provider "aws" {
  alias = "postbox"
  override_resource { target = aws_sesv2_email_identity.sender }
}

mock_provider "yandex" {
  override_resource { target = yandex_iam_service_account.monitoring_ci }
  override_resource { target = yandex_resourcemanager_folder_iam_member.monitoring_ci }
  override_resource { target = yandex_iam_workload_identity_federated_credential.monitoring_ci }
  override_resource { target = yandex_iam_service_account.image_origin }
  override_resource { target = yandex_resourcemanager_folder_iam_member.image_origin_storage }
  override_resource { target = yandex_resourcemanager_folder_iam_member.image_origin_monitoring }
  override_resource { target = yandex_iam_service_account.legacy_monitoring }
  override_resource { target = yandex_resourcemanager_folder_iam_member.legacy_monitoring }
  override_resource { target = yandex_cdn_origin_group.gallery }
  override_resource { target = yandex_cm_certificate.gallery }
  override_resource { target = yandex_vpc_network.origin_default }
  override_resource { target = yandex_vpc_subnet.origin_b }
  override_resource { target = yandex_vpc_security_group.image_origin }
  override_resource { target = yandex_vpc_address.image_origin }
  override_resource { target = yandex_compute_disk.image_origin }
  override_resource { target = yandex_backup_policy.legacy_daily }
  override_resource { target = yandex_backup_policy_bindings.legacy_daily }
  override_resource { target = yandex_iam_service_account.postbox_sender }
  override_resource { target = yandex_resourcemanager_folder_iam_member.postbox_sender }
  override_resource { target = yandex_storage_bucket.private_media }
  override_resource { target = yandex_storage_bucket.public_media }
  override_resource { target = yandex_storage_bucket.selfie_feedback }
  override_resource { target = yandex_storage_bucket.tfstate }
  override_resource { target = yandex_iam_service_account.app }
  override_resource { target = yandex_iam_service_account.hires }
  override_resource { target = yandex_iam_service_account.selfie_feedback }
  override_resource { target = yandex_iam_service_account.lockbox_ci }
  override_resource { target = yandex_iam_service_account.tfstate_backend }
  override_resource { target = yandex_iam_service_account.infrastructure }
  override_resource { target = yandex_resourcemanager_folder_iam_member.adopted["app_storage"] }
  override_resource { target = yandex_resourcemanager_folder_iam_member.adopted["infra_resource"] }
  override_resource { target = yandex_resourcemanager_folder_iam_member.adopted["infra_compute"] }
  override_resource { target = yandex_resourcemanager_folder_iam_member.adopted["infra_iam"] }
  override_resource { target = yandex_resourcemanager_folder_iam_member.adopted["infra_public_vpc"] }
  override_resource { target = yandex_resourcemanager_folder_iam_member.adopted["infra_private_vpc"] }
  override_resource { target = yandex_resourcemanager_folder_iam_member.adopted["infra_security_groups"] }
  override_resource { target = yandex_kms_symmetric_key.selfie_feedback }
  override_resource { target = yandex_kms_symmetric_key_iam_member.selfie_feedback }
  override_resource { target = yandex_lockbox_secret.deployment }
  override_resource { target = yandex_lockbox_secret.vm_deploy }
  override_resource { target = yandex_lockbox_secret.vm_admin }
  override_resource { target = yandex_lockbox_secret_iam_member.adopted["deploy_ci_view"] }
  override_resource { target = yandex_lockbox_secret_iam_member.adopted["deploy_ci_payload"] }
  override_resource { target = yandex_lockbox_secret_iam_member.adopted["vm_deploy_ci_view"] }
  override_resource { target = yandex_lockbox_secret_iam_member.adopted["vm_deploy_ci_payload"] }
  override_resource { target = yandex_lockbox_secret_iam_member.adopted["vm_deploy_infra_view"] }
  override_resource { target = yandex_lockbox_secret_iam_member.adopted["vm_deploy_infra_payload"] }
  override_resource { target = yandex_iam_workload_identity_oidc_federation.deployment }
  override_resource { target = yandex_iam_workload_identity_oidc_federation.infrastructure }
  override_resource { target = yandex_iam_workload_identity_federated_credential.deployment }
  override_resource { target = yandex_iam_workload_identity_federated_credential.infrastructure }
  override_resource { target = yandex_storage_bucket_policy.private_media }
  override_resource { target = yandex_storage_bucket_grant.private_media }
  override_resource { target = yandex_storage_bucket_grant.public_media }
  override_resource { target = yandex_storage_bucket_grant.selfie_feedback }
}

override_resource {
  target = yandex_vpc_address.edge
  values = { external_ipv4_address = { address = "111.88.151.64" } }
}
run "one_recovery_host" {
  command = apply

  variables {
    postbox_access_key   = "offline-test-access-key"
    postbox_secret_key   = "offline-test-secret-key"
    cloud_id             = "b1gmcsmr51o5kvp86l55"
    folder_id            = "b1g2qttgfhb4gdunvlge"
    zone                 = "ru-central1-d"
    ubuntu_image_id      = "fd8p50cvjmb6fo0ik4hk"
    ssh_public_key       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN test"
    admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN admin"
    ssh_cidrs            = ["203.0.113.8/32"]
    boot_disk_gb         = 30
    postgres_disk_gb     = 10
  }

  assert {
    condition     = yandex_compute_instance.app.resources[0].cores == 2 && yandex_compute_instance.app.resources[0].core_fraction == 20 && yandex_compute_instance.app.resources[0].memory == 4
    error_message = "The app host must have 4 GiB RAM and retain 2 vCPU at 20%."
  }

  assert {
    condition     = yandex_compute_instance.app.allow_stopping_for_update == true
    error_message = "Terraform must be allowed to stop the VM to resize its RAM."
  }

  assert {
    condition     = yandex_compute_instance.app.boot_disk[0].auto_delete == false && alltrue([for disk in yandex_compute_instance.app.secondary_disk : disk.auto_delete == false])
    error_message = "Boot and PostgreSQL disks must survive VM deletion."
  }

  assert {
    condition = (
      yandex_compute_disk.postgres.size == 10 &&
      yandex_compute_disk.boot.size == 30 &&
      yandex_compute_disk.postgres.type == "network-ssd" &&
      yandex_compute_disk.boot.type == "network-ssd"
    )
    error_message = "Both reviewed disk sizes and SSD types must reach distinct resources."
  }

  assert {
    condition     = length(yandex_vpc_security_group.vm.ingress) == 4 && toset([for rule in yandex_vpc_security_group.vm.ingress : rule.port]) == toset([80, 443, 8081, 22])
    error_message = "Expected HTTP, HTTPS, health check and SSH rules."
  }

  assert {
    condition     = toset([for l in yandex_lb_network_load_balancer.edge.listener : l.port]) == toset([80, 443]) && alltrue([for l in yandex_lb_network_load_balancer.edge.listener : l.protocol == "tcp" && l.target_port == l.port && one(l.external_address_spec).address == "111.88.151.64"])
    error_message = "NLB must expose only TCP 80/443 on the reserved address."
  }

  assert {
    condition     = one(yandex_lb_target_group.app.target).subnet_id == yandex_vpc_subnet.core_d.id && one(yandex_lb_target_group.app.target).address == yandex_compute_instance.app.network_interface[0].ip_address && one(one(yandex_lb_network_load_balancer.edge.attached_target_group).healthcheck).http_options[0].port == 8081 && one(one(yandex_lb_network_load_balancer.edge.attached_target_group).healthcheck).http_options[0].path == "/health/"
    error_message = "NLB must target the app and check readiness on 8081."
  }

  assert {
    condition     = one([for rule in yandex_vpc_security_group.vm.ingress : rule if rule.port == 8081]).predefined_target == "loadbalancer_healthchecks" && output.ssh_host == yandex_compute_instance.app.network_interface[0].nat_ip_address
    error_message = "Health checks are restricted and SSH stays direct."
  }

  assert {
    condition     = yandex_resourcemanager_folder_iam_member.monitoring_writer.role == "monitoring.editor" && yandex_resourcemanager_folder_iam_member.monitoring_writer.member == "serviceAccount:${yandex_iam_service_account.backup_agent.id}"
    error_message = "The attached VM identity must have monitoring.editor for remote write."
  }

  assert {
    condition     = yandex_resourcemanager_folder_iam_member.backup_agent.role == "backup.user"
    error_message = "Attached VM identity must retain backup.user."
  }

  assert {
    condition     = strcontains(yandex_compute_instance.app.metadata["user-data"], "name: deploy") && strcontains(yandex_compute_instance.app.metadata["user-data"], "name: petrnikitin") && yandex_compute_instance.app.metadata["enable-oslogin"] == "false"
    error_message = "Cloud-init must provision both separate key-only SSH identities with OS Login disabled."
  }
}

run "adopted_resource_contract" {
  command = apply
  variables {
    postbox_access_key   = "offline-test-access-key"
    postbox_secret_key   = "offline-test-secret-key"
    cloud_id             = "b1gmcsmr51o5kvp86l55"
    folder_id            = "b1g2qttgfhb4gdunvlge"
    zone                 = "ru-central1-d"
    ubuntu_image_id      = "fd8p50cvjmb6fo0ik4hk"
    ssh_public_key       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN test"
    admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN admin"
    ssh_cidrs            = ["203.0.113.8/32"]
    boot_disk_gb         = 30
    postgres_disk_gb     = 10
  }
  assert {
    condition = (
      yandex_storage_bucket.private_media.versioning[0].enabled == false &&
      yandex_storage_bucket.selfie_feedback.lifecycle_rule[0].expiration[0].days == 30 &&
      yandex_storage_bucket.selfie_feedback.server_side_encryption_configuration[0].rule[0].apply_server_side_encryption_by_default[0].kms_master_key_id == yandex_kms_symmetric_key.selfie_feedback.id &&
      toset(yandex_storage_bucket.private_media.cors_rule[0].allowed_methods) == toset(["POST"]) &&
      yandex_storage_bucket.public_media.versioning[0].enabled == true
    )
    error_message = "Application storage retention, upload CORS, and KMS encryption drifted."
  }
  assert {
    condition = (
      yandex_kms_symmetric_key.selfie_feedback.deletion_protection &&
      yandex_lockbox_secret.deployment.deletion_protection &&
      yandex_lockbox_secret.vm_deploy.deletion_protection &&
      yandex_lockbox_secret.vm_admin.deletion_protection &&
      yandex_iam_workload_identity_federated_credential.deployment.external_subject_id == "repo:peter-nikitin/photo-prjct:ref:refs/heads/main" &&
      yandex_iam_workload_identity_federated_credential.infrastructure.external_subject_id == "repo:peter-nikitin/photo-prjct:environment:infrastructure"
    )
    error_message = "Deletion protection or GitHub OIDC trust drifted."
  }
  assert {
    condition = (
      yandex_vpc_address.edge.external_ipv4_address[0].zone_id == "ru-central1-b" &&
      yandex_lockbox_secret_iam_member.adopted["vm_deploy_infra_payload"].role == "lockbox.payloadViewer" &&
      yandex_kms_symmetric_key_iam_member.selfie_feedback.role == "kms.keys.encrypterDecrypter"
    )
    error_message = "Existing static IP or narrow IAM grants drifted."
  }
  assert {
    condition = (
      one(yandex_cdn_origin_group.gallery.origin).source == "img-origin.findme-photo.ru" &&
      yandex_cm_certificate.gallery.managed[0].challenge_type == "DNS_CNAME" &&
      yandex_iam_workload_identity_federated_credential.monitoring_ci.external_subject_id == "repo:peter-nikitin/photo-prjct:environment:monitoring" &&
      yandex_compute_disk.image_origin.zone == yandex_vpc_subnet.origin_b.zone &&
      yandex_compute_disk.image_origin.type == "network-hdd"
    )
    error_message = "Retained image-delivery or monitoring identity contract drifted."
  }
  assert {
    condition = (
      one(one(yandex_backup_policy.legacy_daily.retention).rules).max_count == 15 &&
      yandex_backup_policy_bindings.legacy_daily.instance_id == "epdr5g3p24tdns9890nr" &&
      output.retained_cloud_dependencies.monitoring.ownership == "external-controller" &&
      output.retained_cloud_dependencies.monitoring.dashboard_source == "deploy/monitoring/prometheus/single-vm-dashboard.json" &&
      output.retained_cloud_dependencies.legacy_monitoring_alerts.ownership == "pending-review" &&
      output.retained_cloud_dependencies.cdn.ownership == "external-controller" &&
      output.retained_cloud_dependencies.backup_recovery.ownership == "data-and-system-child"
    )
    error_message = "Recovery binding or external-controller ownership drifted."
  }
}

run "postbox_identity_contract" {
  command = apply
  variables {
    postbox_access_key   = "offline-test-access-key"
    postbox_secret_key   = "offline-test-secret-key"
    cloud_id             = "b1gmcsmr51o5kvp86l55"
    folder_id            = "b1g2qttgfhb4gdunvlge"
    zone                 = "ru-central1-d"
    ubuntu_image_id      = "fd8p50cvjmb6fo0ik4hk"
    ssh_public_key       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN test"
    admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN admin"
    ssh_cidrs            = ["203.0.113.8/32"]
    boot_disk_gb         = 30
    postgres_disk_gb     = 10
  }
  assert {
    condition = (
      yandex_iam_service_account.postbox_sender.name == "sa-postbox-sender" &&
      yandex_resourcemanager_folder_iam_member.postbox_sender.role == "postbox.sender" &&
      aws_sesv2_email_identity.sender.email_identity == "findme-photo.ru"
    )
    error_message = "Postbox sender identity, narrow IAM role, or domain changed."
  }
}

run "reject_empty_postbox_management_key" {
  command = plan
  variables {
    postbox_access_key   = "offline-test-access-key"
    postbox_secret_key   = "   "
    cloud_id             = "b1gmcsmr51o5kvp86l55"
    folder_id            = "b1g2qttgfhb4gdunvlge"
    zone                 = "ru-central1-d"
    ubuntu_image_id      = "fd8p50cvjmb6fo0ik4hk"
    ssh_public_key       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN test"
    admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKh9rmX/xkzdkVDBA+LTwNzH9MNjToOjzXvrYZuY7WmN admin"
    ssh_cidrs            = ["203.0.113.8/32"]
    boot_disk_gb         = 30
    postgres_disk_gb     = 10
  }
  expect_failures = [var.postbox_secret_key]
}
