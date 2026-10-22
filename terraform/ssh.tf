# ---------- SSH for debugging (off unless ssh_public_key_path or ssh_key_name is set) ----------
# Port 22 opens only to the IP running terraform apply (ssh_allow_my_ip) and to ssh_cidrs.
# Session Manager (the `shell` output) needs no open port and works whether or not this is on.

resource "aws_key_pair" "debug" {
  count = var.ssh_public_key_path == null ? 0 : 1

  key_name_prefix = "${var.name}-"
  public_key      = trimspace(file(pathexpand(var.ssh_public_key_path)))

  tags = { Name = "${var.name}-debug" }
}

# The public IP of the machine running Terraform, so only you can reach port 22.
data "http" "my_ip" {
  count = local.ssh_enabled && var.ssh_allow_my_ip ? 1 : 0

  url = "https://checkip.amazonaws.com"

  lifecycle {
    postcondition {
      condition     = self.status_code == 200 && can(cidrhost("${trimspace(self.response_body)}/32", 0))
      error_message = "Couldn't look up your public IP at checkip.amazonaws.com. Set ssh_allow_my_ip = false and list your IP in ssh_cidrs instead."
    }
  }
}

locals {
  ssh_key_name = var.ssh_public_key_path != null ? aws_key_pair.debug[0].key_name : var.ssh_key_name
  ssh_enabled  = var.ssh_public_key_path != null || var.ssh_key_name != null
  ssh_cidrs = local.ssh_enabled ? toset(concat(
    var.ssh_cidrs,
    [for ip in data.http.my_ip : "${trimspace(ip.response_body)}/32"],
  )) : toset([])
  # The private key that pairs with ssh_public_key_path, by the usual ssh-keygen naming.
  ssh_identity = var.ssh_public_key_path == null ? null : trimsuffix(var.ssh_public_key_path, ".pub")
}
