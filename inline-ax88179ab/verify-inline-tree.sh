#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "usage: $0 KERNEL_TREE [keep-0x402|driver-only] [pinned|default]" >&2
  exit 2
fi

KERNEL_TREE=$1
QUIRKS=${2:-keep-0x402}
RUNTIME_PM=${3:-pinned}
DRIVER="$KERNEL_TREE/drivers/net/usb/ax_usb_nic"

required_files=(
  "$DRIVER/ax_main.c"
  "$DRIVER/ax_main.h"
  "$DRIVER/ax88179_178a.c"
  "$DRIVER/ax88179a_772d.c"
  "$DRIVER/Makefile"
)
for path in "${required_files[@]}"; do
  test -s "$path" || { echo "missing: $path" >&2; exit 1; }
done

grep -Fq 'config USB_NET_AX_USB_NIC' "$KERNEL_TREE/drivers/net/usb/Kconfig"
grep -Fq 'obj-$(CONFIG_USB_NET_AX_USB_NIC)' "$KERNEL_TREE/drivers/net/usb/Makefile"
grep -Fq 'usb_prefer_ax88179ab_config1' "$KERNEL_TREE/drivers/usb/core/generic.c"
grep -Fq 'selecting ASIX AX88179A/B vendor configuration 1' "$KERNEL_TREE/drivers/usb/core/generic.c"
grep -Fq 'AX88179AB handled by CONFIG_USB_NET_AX_USB_NIC' "$KERNEL_TREE/drivers/net/usb/ax88179_178a.c"
grep -Fq 'Exact AX88179A/B match must precede overlapping legacy ranges' "$DRIVER/ax_main.c"
grep -Fq 'min_t(int, desc->skb_num, remaining)' "$DRIVER/ax_main.c"
grep -Fq 'napi_complete_done(tx_napi, work_done)' "$DRIVER/ax_main.c"
grep -Fq '#if LINUX_VERSION_CODE < KERNEL_VERSION(4, 10, 0)' "$DRIVER/ax_main.h"
if grep -Fq 'return -1;' "$DRIVER/ax_main.c"; then
  echo "warning: source still contains return -1; inspect whether unrelated" >&2
fi

if [[ $RUNTIME_PM == pinned ]]; then
  grep -Fq 'AX88179A/B runtime PM pinned active' "$DRIVER/ax_main.c"
  grep -Fq 'pm_runtime_forbid(&udev->dev)' "$DRIVER/ax_main.c"
  grep -Fq 'ax_pin_88179ab_runtime_pm(udev);' "$DRIVER/ax_main.c"
fi

if [[ $QUIRKS == keep-0x402 ]]; then
  grep -Fq 'USB_DEVICE_VER(0x0b95, 0x1790, 0x0200, 0x0200)' "$KERNEL_TREE/drivers/usb/core/quirks.c"
  grep -Fq 'USB_QUIRK_RESET_RESUME | USB_QUIRK_NO_LPM' "$KERNEL_TREE/drivers/usb/core/quirks.c"
else
  if grep -Fq 'USB_DEVICE_VER(0x0b95, 0x1790, 0x0200, 0x0200)' "$KERNEL_TREE/drivers/usb/core/quirks.c"; then
    echo "unexpected retained exact quirk entry" >&2
    exit 1
  fi
fi

printf '%s\n' 'inline tree verification passed'
