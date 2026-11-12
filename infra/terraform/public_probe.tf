resource "yandex_iam_service_account" "public_probe" {
  folder_id = var.folder_id
  name      = "findme-photo-public-probe"
}

resource "yandex_resourcemanager_folder_iam_member" "public_probe_writer" {
  folder_id = var.folder_id
  role      = "monitoring.editor"
  member    = "serviceAccount:${yandex_iam_service_account.public_probe.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "infrastructure_functions" {
  folder_id = var.folder_id
  # The release account also manages the private function's access bindings.
  role   = "functions.admin"
  member = "serviceAccount:ajema849qjho2lgjaqvo"
}

resource "yandex_iam_service_account_iam_member" "infrastructure_public_probe" {
  service_account_id = yandex_iam_service_account.public_probe.id
  role               = "iam.serviceAccounts.user"
  member             = "serviceAccount:ajema849qjho2lgjaqvo"
}

resource "yandex_function" "public_probe" {
  folder_id          = var.folder_id
  name               = "findme-photo-public-probe"
  description        = "Check canonical public DNS, TLS and HTTP independently of the app VM."
  runtime            = "python312"
  entrypoint         = "index.handler"
  memory             = 128
  execution_timeout  = "20"
  service_account_id = yandex_iam_service_account.public_probe.id
  user_hash          = filesha256("${path.module}/public-probe.zip")

  environment = {
    PROMETHEUS_WORKSPACE_ID = "mon0c97qv2s5uju1ark8"
  }

  content {
    zip_filename = "${path.module}/public-probe.zip"
  }

  # Invocation errors remain visible in the platform's function metrics.
  # The checker neither creates a log group nor retains request/credential data.
  log_options {
    disabled = true
  }

  depends_on = [
    yandex_resourcemanager_folder_iam_member.public_probe_writer,
    yandex_resourcemanager_folder_iam_member.infrastructure_functions,
    yandex_iam_service_account_iam_member.infrastructure_public_probe,
  ]
}

resource "yandex_function_iam_binding" "public_probe_invoker" {
  function_id = yandex_function.public_probe.id
  role        = "functions.functionInvoker"
  members     = ["serviceAccount:${yandex_iam_service_account.public_probe.id}"]
}

resource "yandex_function_trigger" "public_probe" {
  folder_id = var.folder_id
  name      = "findme-photo-public-probe"

  timer {
    cron_expression = "* * * * ? *"
  }

  function {
    id                 = yandex_function.public_probe.id
    service_account_id = yandex_iam_service_account.public_probe.id
  }

  depends_on = [yandex_function_iam_binding.public_probe_invoker]
}
