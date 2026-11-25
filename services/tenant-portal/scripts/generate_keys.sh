#!/bin/sh
# Generate the RS256 key pair used to sign (private) and verify (public) JWTs.
# Usage: ./scripts/generate_keys.sh [--force]
set -eu

KEY_DIR="$(cd "$(dirname "$0")/.." && pwd)/keys"
PRIVATE_KEY="$KEY_DIR/jwt_private.pem"
PUBLIC_KEY="$KEY_DIR/jwt_public.pem"

if [ -f "$PRIVATE_KEY" ] && [ "${1:-}" != "--force" ]; then
    echo "Keys already exist in $KEY_DIR (use --force to overwrite)." >&2
    exit 1
fi

mkdir -p "$KEY_DIR"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$PRIVATE_KEY"
openssl pkey -in "$PRIVATE_KEY" -pubout -out "$PUBLIC_KEY"
# The container runs as uid 1000 and needs to read the private key via the
# read-only bind mount; keep it unreadable for other host users.
chmod 600 "$PRIVATE_KEY"
chmod 644 "$PUBLIC_KEY"

echo "Wrote $PRIVATE_KEY and $PUBLIC_KEY"
#Purpose: Generates the RSA key pair used to
#sign JWTs (RS256)