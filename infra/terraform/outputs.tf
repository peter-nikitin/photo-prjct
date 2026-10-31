output "ssh_host" {
  value = yandex_vpc_address.site.external_ipv4_address[0].address
}

output "direct_ssh_host" {
  value = yandex_compute_instance.app.network_interface[0].nat_ip_address
}

output "vm_id" {
  value = yandex_compute_instance.app.id
}

output "subnet_id" {
  value = yandex_vpc_subnet.core_d.id
}

output "private_ip" {
  value = yandex_compute_instance.app.network_interface[0].ip_address
}

output "postgres_disk_id" {
  value = yandex_compute_disk.postgres.id
}

output "postgres_disk_device" {
  value = "/dev/disk/by-id/virtio-pgdata"
}
