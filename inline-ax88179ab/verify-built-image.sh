#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 KERNEL_TREE IMAGE" >&2
  exit 2
fi

KERNEL_TREE=$1
IMAGE=$2
CONFIG=$(mktemp)
IMAGE_STRINGS=$(mktemp)
trap 'rm -f "$CONFIG" "$IMAGE_STRINGS"' EXIT

test -s "$IMAGE"
"$KERNEL_TREE/scripts/extract-ikconfig" "$IMAGE" > "$CONFIG"
grep -Fqx 'CONFIG_USB_NET_DRIVERS=y' "$CONFIG"
grep -Fqx 'CONFIG_MII=y' "$CONFIG"
grep -Fqx 'CONFIG_USB_NET_AX_USB_NIC=y' "$CONFIG"
grep -Fqx 'CONFIG_USB_NET_AX_USB_NIC_PIN_RUNTIME_PM=y' "$CONFIG"

# Write strings once. This avoids grep -q causing SIGPIPE under pipefail.
strings "$IMAGE" > "$IMAGE_STRINGS"
grep -Fq 'selecting ASIX AX88179A/B vendor configuration 1' "$IMAGE_STRINGS"
grep -Fq 'AX88179A/B runtime PM pinned active' "$IMAGE_STRINGS"
grep -Fq 'ax_usb_nic' "$IMAGE_STRINGS"

printf '%s\n' "built Image contains the fully inlined AX88179A/B implementation: $IMAGE"
