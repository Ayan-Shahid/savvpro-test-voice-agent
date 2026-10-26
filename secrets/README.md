# Secrets

Only SOPS-encrypted files (`<env>.enc.yaml`) belong here. `runtime/` holds decrypted Docker secret files
on the deploy host and is git-ignored. See `scripts/secrets.sh` and `.sops.yaml`.
