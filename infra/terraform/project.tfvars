cloud_id                          = "b1gmcsmr51o5kvp86l55"
folder_id                         = "b1g2qttgfhb4gdunvlge"
infrastructure_service_account_id = "ajema849qjho2lgjaqvo"
zone                              = "ru-central1-d"
ubuntu_image_id                   = "fd8a30ptg5tdapq35dlb"
boot_disk_gb                      = 30
postgres_disk_gb                  = 10
ssh_cidrs                         = ["0.0.0.0/0"]
# Explicit provisional bootstrap sizes, not measured restore capacity. Boot 30 GiB
# covers current heavy image pulls, 2 GiB swap and agent install beyond Ubuntu's
# 10 GiB image minimum; review measured peak and cost before live plan. PG 10 GiB
# is an empty initial disk. Resize through a reviewed Terraform PR before restore
# if actual data, WAL, tablespaces and scratch require more space.
# SSH is key-only with non-root admin and authenticated host-key pinning. This
# explicit IPv4 range permits changing GitHub-hosted runner source addresses;
# narrow it through another reviewed PR when a stable CI egress exists.
# Operator supplies only ssh_public_key through the matching CI var.
