#!/usr/bin/env bash
# Secrets workflow (SOPS + age). Plaintext never enters git: only secrets/<env>.enc.yaml is committed.
#   scripts/secrets.sh init <env>     generate strong random values and write secrets/<env>.enc.yaml
#   scripts/secrets.sh edit <env>     edit in $EDITOR (decrypted in memory by sops)
#   scripts/secrets.sh render <env>   decrypt into secrets/runtime/<name> files (0600) for Docker secrets
#   scripts/secrets.sh local          development only: random runtime files without SOPS
set -euo pipefail
cd "$(dirname "$0")/.."
NAMES=(db_superuser_password db_auth_password db_owner_password db_api_password db_worker_password
       redis_api_password redis_worker_password jwt_secret seed_demo_password)
OPTIONAL=(deepgram_api_key smtp_password cloudflared_token google_client_secret)
rand() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "${1:-40}"; }

case "${1:-}" in
  init)
    env="${2:?env}"; out="secrets/${env}.enc.yaml"
    [[ -e "$out" ]] && { echo "$out exists; use edit"; exit 1; }
    tmp=$(mktemp); trap 'shred -u "$tmp" 2>/dev/null || rm -f "$tmp"' EXIT
    { for n in "${NAMES[@]}"; do echo "$n: $(rand 40)"; done
      echo "jwt_secret_note: rotate by changing jwt_secret; every session ends"
      for n in "${OPTIONAL[@]}"; do echo "$n: \"\""; done; } > "$tmp"
    sed -i '/^jwt_secret_note/d' "$tmp"
    sops --encrypt --filename-override "$out" --input-type yaml --output-type yaml "$tmp" > "$out"
    echo "wrote $out — now add deepgram_api_key, smtp_password and cloudflared_token with: $0 edit $env" ;;
  edit)
    sops "secrets/${2:?env}.enc.yaml" ;;
  render)
    env="${2:?env}"; dir=secrets/runtime; umask 077; mkdir -p "$dir"
    sops --decrypt --output-type json "secrets/${env}.enc.yaml" \
      | python3 -c 'import json,sys,os
d=json.load(sys.stdin)
for k,v in d.items():
    if k=="sops": continue
    p=os.path.join(sys.argv[1],k)
    with open(p,"w") as f: f.write(str(v))
    os.chmod(p,0o600)
print("rendered",len(d)-1,"secrets")' "$dir"
    for n in "${OPTIONAL[@]}"; do [[ -e "$dir/$n" ]] || : > "$dir/$n"; done ;;
  local)
    dir=secrets/runtime; umask 077; mkdir -p "$dir"
    for n in "${NAMES[@]}"; do [[ -s "$dir/$n" ]] || rand 40 > "$dir/$n"; done
    for n in "${OPTIONAL[@]}"; do [[ -e "$dir/$n" ]] || : > "$dir/$n"; done
    echo "development secrets in $dir (put a Deepgram key in $dir/deepgram_api_key for voice)" ;;
  *) sed -n '2,7p' "$0"; exit 1 ;;
esac
