"""Run Terraform contracts in isolated directories with local mock providers only."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def compact(text: str) -> str:
    """Collapse whitespace so assertions survive terraform fmt."""
    return re.sub(r"\s+", " ", text)


class TerraformContracts(unittest.TestCase):
    @unittest.skipUnless(shutil.which("terraform"), "Terraform is not installed")
    def test_mock_provider_contracts(self):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("TF_", "ARM_", "AZURE_", "CLOUDFLARE_"))}
        environment.update(TF_IN_AUTOMATION="1", CHECKPOINT_DISABLE="1")
        for root_name in ("terraform", "terraform-erpnext", "terraform-erpbridge"):
            source = ROOT / root_name
            providers = source / ".terraform" / "providers"
            lock = source / ".terraform.lock.hcl"
            if not providers.is_dir() or not lock.is_file():
                self.fail(f"Initialize {root_name} with its checked-in lock before running contracts")
            with self.subTest(root=root_name), tempfile.TemporaryDirectory(prefix="terraform-contracts-") as directory:
                target = Path(directory)
                # Never copy state, real tfvars, backend configuration, or credentials.
                for path in source.glob("*.tf"):
                    shutil.copy2(path, target / path.name)
                shutil.copy2(lock, target / ".terraform.lock.hcl")
                shutil.copytree(source / "tests", target / "tests")
                # Terraform reads only these reviewed source assets. Never copy
                # the complete root, real variable files, state, or credentials.
                for asset in ("monitoring", "monitoring-schema.json"):
                    path = source / asset
                    if path.is_dir():
                        shutil.copytree(path, target / asset)
                    elif path.is_file():
                        shutil.copy2(path, target / asset)
                # Roots that build custom_data need their cloud-init file. It
                # contains packages and hardening only, never credentials.
                cloud_init = source / "cloud-init.yaml"
                if cloud_init.is_file():
                    shutil.copy2(cloud_init, target / cloud_init.name)
                (target / ".terraform").mkdir()
                (target / ".terraform" / "providers").symlink_to(providers.resolve(), target_is_directory=True)
                for command in ("validate", "test"):
                    result = subprocess.run(
                        ["terraform", f"-chdir={target}", command, "-no-color"],
                        env=environment, text=True, capture_output=True, timeout=120,
                    )
                    self.assertEqual(result.returncode, 0, f"{root_name} {command}:\n" + result.stdout + result.stderr)

    def test_erpbridge_hosting_contracts(self):
        """The hosting switch is explicit, split from certificates, and cannot reopen quietly."""
        root = ROOT / "terraform"
        main = compact((root / "main.tf").read_text())
        variables = compact((root / "variables.tf").read_text())
        domains = compact((root / "custom-domains.tf").read_text())
        outputs = compact((root / "outputs.tf").read_text())
        # The bridge ingress is closed whenever the source is fenced, so an
        # ordinary later apply cannot restore public serving on its own.
        self.assertIn("external_enabled = !var.erpbridge_source_fenced", main)
        # MockERP keeps its unconditional public ingress.
        self.assertIn("external_enabled = true target_port = local.mockerp_port", main)
        # DNS record content comes from the split local, never straight from the
        # Container App FQDN, so an A record can replace the CNAME safely.
        self.assertIn("type = each.value.record_type", domains)
        self.assertIn("content = each.value.content", domains)
        self.assertNotIn("content = each.value.app_fqdn", domains)
        # The CNAME wait is gated instead of removed, so the transition plans no
        # destroys and rollback only re-enables the check.
        self.assertIn("each.value.verify ?", domains)
        for name in ("erpbridge_hosting", "erpbridge_vm_ipv4", "erpbridge_source_fenced"):
            self.assertIn(f'variable "{name}"', variables)
        # VM hosting without the fence, or without a valid address, is refused.
        self.assertIn('var.erpbridge_hosting != "vm" || var.erpbridge_source_fenced', variables)
        self.assertIn('var.erpbridge_hosting != "vm" || (', variables)
        self.assertIn('output "erpbridge_hosting"', outputs)
        self.assertIn("hosting = var.erpbridge_hosting", outputs)
        self.assertIn("source_fenced = var.erpbridge_source_fenced", outputs)

    def test_erpbridge_vm_static_contracts(self):
        """The new root stays isolated, non-destructive, and free of secrets."""
        root = ROOT / "terraform-erpbridge"
        self.assertTrue((root / "tests" / "fixtures" / "operator.pub").is_file(),
                        "the isolated runner needs the SSH public-key fixture")
        terraform_text = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(root.glob("*.tf"))
        )
        lowered = terraform_text.lower()
        # Reuse the existing resource group through a data source only.
        self.assertIn('data "azurerm_resource_group"', terraform_text)
        self.assertNotIn('resource "azurerm_resource_group"', terraform_text)
        # No separate data disk, no attachment, no other workload, and no DNS.
        # Match real provider or resource usage, not explanatory comments.
        for forbidden in ("azurerm_managed_disk", "data_disk", "cloudflare_", "provider \"cloudflare\"",
                          "erpnext", "azurerm_dns_"):
            self.assertNotIn(forbidden, lowered)
        # An accidental VM deletion must retain the OS disk.
        self.assertIn("delete_os_disk_on_deletion = false", terraform_text)
        self.assertIn('resource_provider_registrations = "none"', terraform_text)
        # Cloud-init installs packages and hardens SSH; it never runs the app.
        cloud_init = (root / "cloud-init.yaml").read_text(encoding="utf-8")
        for package in ("docker.io", "docker-compose-v2", "caddy", "ca-certificates"):
            self.assertIn(package, cloud_init)
        for forbidden in ("docker pull", "docker run", "docker login", "docker compose up"):
            self.assertNotIn(forbidden, cloud_init.lower())
        for pattern in (r"(?m)^\s*(password|passwd|secret|token|api_key|private_key)\s*:",
                        r"PRIVATE KEY-----"):
            self.assertIsNone(re.search(pattern, cloud_init, re.IGNORECASE),
                              "cloud-init must not contain credentials")


if __name__ == "__main__":
    unittest.main()
