# Config-driven imports only for addresses newly defined in this adoption.
import {
  to = yandex_storage_bucket.private_media
  id = "hires-staging"
}
import {
  to = yandex_storage_bucket.public_media
  id = "findme-photo-public-media-b1g2qttg"
}
import {
  to = yandex_storage_bucket.selfie_feedback
  id = "findme-selfie-feedback-staging-b1g2qttg"
}
import {
  to = yandex_storage_bucket.tfstate
  id = "findme-photo-tfstate"
}

import {
  to = yandex_iam_service_account.app
  id = "ajejo91its6ckh9ch3gq"
}
import {
  to = yandex_iam_service_account.hires
  id = "ajeg9msqqkanrlgqqr1f"
}
import {
  to = yandex_iam_service_account.selfie_feedback
  id = "aje39ip0j620c1l6qa7d"
}
import {
  to = yandex_iam_service_account.lockbox_ci
  id = "ajeaekiue94ogksguh0h"
}
import {
  to = yandex_iam_service_account.tfstate_backend
  id = "ajeonsj2akeimj92fokf"
}
import {
  to = yandex_iam_service_account.infrastructure
  id = "ajema849qjho2lgjaqvo"
}

import {
  to = yandex_resourcemanager_folder_iam_member.adopted["app_storage"]
  id = "b1g2qttgfhb4gdunvlge,storage.editor,serviceAccount:ajejo91its6ckh9ch3gq"
}
import {
  to = yandex_resourcemanager_folder_iam_member.adopted["infra_resource"]
  id = "b1g2qttgfhb4gdunvlge,resource-manager.admin,serviceAccount:ajema849qjho2lgjaqvo"
}
import {
  to = yandex_resourcemanager_folder_iam_member.adopted["infra_compute"]
  id = "b1g2qttgfhb4gdunvlge,compute.editor,serviceAccount:ajema849qjho2lgjaqvo"
}
import {
  to = yandex_resourcemanager_folder_iam_member.adopted["infra_iam"]
  id = "b1g2qttgfhb4gdunvlge,iam.serviceAccounts.admin,serviceAccount:ajema849qjho2lgjaqvo"
}
import {
  to = yandex_resourcemanager_folder_iam_member.adopted["infra_public_vpc"]
  id = "b1g2qttgfhb4gdunvlge,vpc.publicAdmin,serviceAccount:ajema849qjho2lgjaqvo"
}
import {
  to = yandex_resourcemanager_folder_iam_member.adopted["infra_private_vpc"]
  id = "b1g2qttgfhb4gdunvlge,vpc.privateAdmin,serviceAccount:ajema849qjho2lgjaqvo"
}
import {
  to = yandex_resourcemanager_folder_iam_member.adopted["infra_security_groups"]
  id = "b1g2qttgfhb4gdunvlge,vpc.securityGroups.admin,serviceAccount:ajema849qjho2lgjaqvo"
}

import {
  to = yandex_kms_symmetric_key.selfie_feedback
  id = "abjjca35o900fng2nk6v"
}
import {
  to = yandex_kms_symmetric_key_iam_member.selfie_feedback
  id = "abjjca35o900fng2nk6v,kms.keys.encrypterDecrypter,serviceAccount:aje39ip0j620c1l6qa7d"
}
import {
  to = yandex_lockbox_secret.deployment
  id = "e6q85jjl76r45maigtfb"
}
import {
  to = yandex_lockbox_secret.vm_deploy
  id = "e6q12ru8opfn2sl91so3"
}
import {
  to = yandex_lockbox_secret.vm_admin
  id = "e6qes463bl6r09r72580"
}
import {
  to = yandex_lockbox_secret_iam_member.adopted["deploy_ci_view"]
  id = "e6q85jjl76r45maigtfb,lockbox.viewer,serviceAccount:ajeaekiue94ogksguh0h"
}
import {
  to = yandex_lockbox_secret_iam_member.adopted["deploy_ci_payload"]
  id = "e6q85jjl76r45maigtfb,lockbox.payloadViewer,serviceAccount:ajeaekiue94ogksguh0h"
}
import {
  to = yandex_lockbox_secret_iam_member.adopted["vm_deploy_ci_view"]
  id = "e6q12ru8opfn2sl91so3,lockbox.viewer,serviceAccount:ajeaekiue94ogksguh0h"
}
import {
  to = yandex_lockbox_secret_iam_member.adopted["vm_deploy_ci_payload"]
  id = "e6q12ru8opfn2sl91so3,lockbox.payloadViewer,serviceAccount:ajeaekiue94ogksguh0h"
}
import {
  to = yandex_lockbox_secret_iam_member.adopted["vm_deploy_infra_view"]
  id = "e6q12ru8opfn2sl91so3,lockbox.viewer,serviceAccount:ajema849qjho2lgjaqvo"
}
import {
  to = yandex_lockbox_secret_iam_member.adopted["vm_deploy_infra_payload"]
  id = "e6q12ru8opfn2sl91so3,lockbox.payloadViewer,serviceAccount:ajema849qjho2lgjaqvo"
}

import {
  to = yandex_iam_workload_identity_oidc_federation.deployment
  id = "ajeula3gd46omgf9jiko"
}
import {
  to = yandex_iam_workload_identity_oidc_federation.infrastructure
  id = "aje2m3i6k60tdau0u9vk"
}
import {
  to = yandex_iam_workload_identity_federated_credential.deployment
  id = "ajej11dnec456ucpu1s3"
}
import {
  to = yandex_iam_workload_identity_federated_credential.infrastructure
  id = "ajeo1rvk92qc8s7vnjd6"
}

import {
  to = yandex_storage_bucket_policy.private_media
  id = "hires-staging"
}

import {
  to = yandex_storage_bucket_grant.private_media
  id = "hires-staging"
}

import {
  to = yandex_storage_bucket_grant.public_media
  id = "findme-photo-public-media-b1g2qttg"
}

import {
  to = yandex_storage_bucket_grant.selfie_feedback
  id = "findme-selfie-feedback-staging-b1g2qttg"
}

import {
  to = yandex_iam_service_account.postbox_sender
  id = "ajenr791754j1aegg2o5"
}
import {
  to = yandex_resourcemanager_folder_iam_member.postbox_sender
  id = "b1g2qttgfhb4gdunvlge,postbox.sender,serviceAccount:ajenr791754j1aegg2o5"
}
import {
  to = aws_sesv2_email_identity.sender
  id = "findme-photo.ru"
}

import {
  to = yandex_iam_service_account.monitoring_ci
  id = "aje3t70qka1dtc09k5ic"
}

import {
  to = yandex_resourcemanager_folder_iam_member.monitoring_ci
  id = "b1g2qttgfhb4gdunvlge,monitoring.editor,serviceAccount:aje3t70qka1dtc09k5ic"
}

import {
  to = yandex_iam_workload_identity_federated_credential.monitoring_ci
  id = "ajeprb31m2nhu6pbj2gn"
}

import {
  to = yandex_iam_service_account.image_origin
  id = "ajef0cammpfg75gn7ntd"
}

import {
  to = yandex_resourcemanager_folder_iam_member.image_origin_storage
  id = "b1g2qttgfhb4gdunvlge,storage.viewer,serviceAccount:ajef0cammpfg75gn7ntd"
}

import {
  to = yandex_resourcemanager_folder_iam_member.image_origin_monitoring
  id = "b1g2qttgfhb4gdunvlge,monitoring.editor,serviceAccount:ajef0cammpfg75gn7ntd"
}

import {
  to = yandex_iam_service_account.legacy_monitoring
  id = "aje62dg0p7tpn6tqu67d"
}

import {
  to = yandex_resourcemanager_folder_iam_member.legacy_monitoring
  id = "b1g2qttgfhb4gdunvlge,monitoring.editor,serviceAccount:aje62dg0p7tpn6tqu67d"
}

import {
  to = yandex_cdn_origin_group.gallery
  id = "2569228330826683012"
}

import {
  to = yandex_cm_certificate.gallery
  id = "fpqkhstccrsfl899lq4h"
}

import {
  to = yandex_vpc_network.origin_default
  id = "enpevjgdgdavmrv9ahb8"
}

import {
  to = yandex_vpc_subnet.origin_b
  id = "e2l18k26thgtq8vobbq7"
}

import {
  to = yandex_vpc_security_group.image_origin
  id = "enphgh6s669dv1647ggv"
}

import {
  to = yandex_vpc_address.image_origin
  id = "e2l0lvoheorael320u6u"
}

import {
  to = yandex_compute_disk.image_origin
  id = "epd07hfr77d4a3un6n9d"
}


import {
  to = yandex_backup_policy.legacy_daily
  id = "cdg3zbstfcpxlmyrxm3a"
}

import {
  to = yandex_backup_policy_bindings.legacy_daily
  id = "cdg3zbstfcpxlmyrxm3a:epdr5g3p24tdns9890nr"
}
