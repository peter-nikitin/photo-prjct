variable "cloud_id" {
  type = string
  validation {
    condition     = var.cloud_id == "b1gmcsmr51o5kvp86l55"
    error_message = "Review cloud identity explicitly before changing it."
  }
}

variable "folder_id" {
  type = string
  validation {
    condition     = var.folder_id == "b1g2qttgfhb4gdunvlge"
    error_message = "Review core folder identity explicitly before changing it."
  }
}

variable "infrastructure_service_account_id" {
  type        = string
  description = "Existing CI service account authorized to plan and release infrastructure."
  validation {
    condition     = var.infrastructure_service_account_id == "ajema849qjho2lgjaqvo"
    error_message = "Review infrastructure service account identity explicitly before changing it."
  }
}

variable "zone" {
  type = string
  validation {
    condition     = var.zone == "ru-central1-d"
    error_message = "Stage 1 uses ru-central1-d."
  }
}

variable "ubuntu_image_id" {
  type        = string
  description = "Pinned Ubuntu 24.04 x86-64 image, reviewed before a replacement."
}

variable "ssh_public_key" {
  type        = string
  description = "Public OpenSSH key only; supply through TF_VAR_ssh_public_key or an untracked local tfvars file."
  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/]+={0,3}( .*)?$", trimspace(var.ssh_public_key)))
    error_message = "Provide one public OpenSSH key."
  }
}

variable "ssh_cidrs" {
  type        = list(string)
  description = "Reviewed administrative IPv4 source CIDRs in tracked project.tfvars. Stage 1 allows dynamic GitHub-hosted runner addresses with key-only SSH."
  validation {
    condition     = length(var.ssh_cidrs) > 0 && alltrue([for cidr in var.ssh_cidrs : can(cidrnetmask(cidr))])
    error_message = "Provide at least one valid IPv4 CIDR."
  }
}

variable "boot_disk_gb" {
  type        = number
  description = "Reviewed boot size from tracked project.tfvars. Current 30 GiB is provisional; image minimum is 10 GiB and total usage remains unmeasured."
  validation {
    condition     = var.boot_disk_gb >= 10
    error_message = "Boot disk must meet the pinned image minimum of 10 GiB."
  }
}

variable "postgres_disk_gb" {
  type        = number
  description = "Persistent PostgreSQL only. Tracked 10 GiB is an empty bootstrap disk; measure recovered DB and review a resize plan before restore."
  validation {
    condition     = var.postgres_disk_gb >= 10
    error_message = "PostgreSQL disk must be at least 10 GiB."
  }
}
