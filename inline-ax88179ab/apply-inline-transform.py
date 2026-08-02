#!/usr/bin/env python3
"""Inline ASIX ax_usb_nic 4.1.0 into an Android/Linux 6.1 kernel tree.

The script is intentionally strict and idempotent. It copies the pinned official
ASIX source, adds an in-tree Kconfig/Makefile integration, and applies targeted
fixes for the exact AX88179A/B device 0b95:1790, bcdDevice 0x0200.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ASIX_COMMIT = "6887878a1c67cc24789f18e31b87db5116d7b6f7"
ASIX_FILES = (
    "ax_config.h",
    "ax_ioctl.h",
    "ax_main.c",
    "ax_main.h",
    "ax88179_178a.c",
    "ax88179_178a.h",
    "ax88179a_772d.c",
    "ax88179a_772d.h",
    "ax_ptp.c",
    "ax_ptp.h",
)


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def read(path: Path) -> str:
    if not path.is_file():
        die(f"missing required file: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0 and new in text:
        return text
    if count != 1:
        die(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def insert_before_once(text: str, marker: str, addition: str, label: str) -> str:
    if addition.strip() in text:
        return text
    count = text.count(marker)
    if count != 1:
        die(f"{label}: expected one marker, found {count}")
    return text.replace(marker, addition + marker, 1)


def verify_asix_source(asix: Path) -> None:
    for name in ASIX_FILES:
        if not (asix / name).is_file():
            die(f"ASIX source is incomplete: missing {name}")

    version = read(asix / "ax_main.h")
    if 'DRIVER_VERSION' not in version or '"4.1.0"' not in version:
        die("ASIX source is not the expected ax_usb_nic 4.1.0 release")

    git_dir = asix / ".git"
    if git_dir.exists():
        actual = subprocess.check_output(
            ["git", "-C", str(asix), "rev-parse", "HEAD"], text=True
        ).strip()
        if actual != ASIX_COMMIT:
            die(f"ASIX source commit mismatch: expected {ASIX_COMMIT}, got {actual}")


def copy_driver(kernel: Path, asix: Path) -> Path:
    destination = kernel / "drivers/net/usb/ax_usb_nic"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ASIX_FILES:
        shutil.copy2(asix / name, destination / name)

    write(
        destination / "Makefile",
        "# SPDX-License-Identifier: GPL-2.0\n"
        "obj-$(CONFIG_USB_NET_AX_USB_NIC) += ax_usb_nic.o\n"
        "ax_usb_nic-y := ax_main.o ax88179_178a.o ax88179a_772d.o ax_ptp.o\n",
    )
    return destination


def integrate_kbuild(kernel: Path) -> None:
    kconfig_path = kernel / "drivers/net/usb/Kconfig"
    kconfig = read(kconfig_path)
    block = """
config USB_NET_AX_USB_NIC
\tbool "ASIX AX88179A/B/AX88772D/AX88279 vendor USB NIC driver"
\tdepends on USB
\tselect MII
\thelp
\t  Build ASIX ax_usb_nic 4.1.0 directly into the kernel.  This is the
\t  AX88179A/B-aware vendor driver used for USB configuration 1.  The
\t  exact AX88179A/B revision 0b95:1790, bcdDevice 0x0200 is selected
\t  before the generic CDC-NCM configuration.

config USB_NET_AX_USB_NIC_PIN_RUNTIME_PM
\tbool "Keep AX88179A/B runtime-active"
\tdepends on USB_NET_AX_USB_NIC
\tdefault y
\thelp
\t  For 0b95:1790 revision 0x0200, disable USB autosuspend and forbid
\t  runtime PM for the lifetime of the attached device.  This is the
\t  in-kernel equivalent of writing 'on' to power/control.

"""
    if "config USB_NET_AX_USB_NIC\n" not in kconfig:
        marker = "endif # USB_NET_DRIVERS\n"
        if marker in kconfig:
            kconfig = kconfig.replace(marker, block + marker, 1)
        else:
            kconfig += "\n" + block
        write(kconfig_path, kconfig)

    makefile_path = kernel / "drivers/net/usb/Makefile"
    makefile = read(makefile_path)
    line = "obj-$(CONFIG_USB_NET_AX_USB_NIC)\t+= ax_usb_nic/\n"
    if line not in makefile:
        anchor = "obj-$(CONFIG_USB_NET_AX88179_178A)\t+= ax88179_178a.o\n"
        if anchor in makefile:
            makefile = makefile.replace(anchor, line + anchor, 1)
        else:
            makefile += "\n" + line
        write(makefile_path, makefile)


def patch_generic_config_choice(kernel: Path) -> None:
    path = kernel / "drivers/usb/core/generic.c"
    text = read(path)
    helper = """
static bool usb_prefer_ax88179ab_config1(struct usb_device *udev)
{
\treturn IS_ENABLED(CONFIG_USB_NET_AX_USB_NIC) &&
\t\tle16_to_cpu(udev->descriptor.idVendor) == 0x0b95 &&
\t\tle16_to_cpu(udev->descriptor.idProduct) == 0x1790 &&
\t\tle16_to_cpu(udev->descriptor.bcdDevice) == 0x0200;
}

"""
    text = insert_before_once(
        text,
        "int usb_choose_configuration(struct usb_device *udev)\n",
        helper,
        "USB configuration helper",
    )

    old = """\tbest = NULL;
\tc = udev->config;
\tnum_configs = udev->descriptor.bNumConfigurations;
\tfor (i = 0; i < num_configs; (i++, c++)) {
"""
    new = """\tbest = NULL;
\tc = udev->config;
\tnum_configs = udev->descriptor.bNumConfigurations;

\t/*
\t * AX88179A/B exposes vendor configuration 1 and CDC-NCM
\t * configuration 2.  The generic chooser normally prefers the latter
\t * because it is class-based.  When the in-tree vendor driver is built,
\t * choose the tested vendor configuration before any interface driver can
\t * claim CDC-NCM.
\t */
\tif (usb_prefer_ax88179ab_config1(udev)) {
\t\tfor (i = 0; i < num_configs; i++) {
\t\t\tc = &udev->config[i];
\t\t\tif (c->desc.bConfigurationValue != 1)
\t\t\t\tcontinue;
\t\t\tif (usb_get_max_power(udev, c) > udev->bus_mA)
\t\t\t\tbreak;
\t\t\tdev_info(&udev->dev,
\t\t\t\t "selecting ASIX AX88179A/B vendor configuration 1\\n");
\t\t\treturn 1;
\t\t}
\t\tdev_warn(&udev->dev,
\t\t\t "AX88179A/B vendor configuration 1 unavailable\\n");
\t}

\tc = udev->config;
\tfor (i = 0; i < num_configs; (i++, c++)) {
"""
    text = replace_once(text, old, new, "USB configuration preference")
    write(path, text)


def patch_legacy_driver_conflict(kernel: Path) -> None:
    path = kernel / "drivers/net/usb/ax88179_178a.c"
    text = read(path)
    if "AX88179AB handled by CONFIG_USB_NET_AX_USB_NIC" in text:
        return

    # Android 6.1 matches the legacy implementation by both VID/PID and the
    # vendor-specific configuration-1 interface.  Remove only that exact table
    # element while the new built-in implementation is enabled; retain the old
    # driver and all of its other device IDs.
    pattern = re.compile(
        r"(?P<entry>\{\s*"
        r"/\*\s*ASIX AX88179 10/100/1000\s*\*/\s*"
        r"USB_DEVICE_AND_INTERFACE_INFO\(\s*0x0b95\s*,\s*0x1790\s*,"
        r"\s*0xff\s*,\s*0xff\s*,\s*0\s*\)\s*,\s*"
        r"\.driver_info\s*=\s*\(unsigned long\)&ax88179_info\s*,\s*"
        r"\})\s*,\s*\{",
        re.DOTALL | re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        die(f"legacy AX88179 entry: expected one match, found {len(matches)}")

    entry = matches[0].group("entry")
    wrapped = (
        "#ifndef CONFIG_USB_NET_AX_USB_NIC\n"
        "/* AX88179AB handled by CONFIG_USB_NET_AX_USB_NIC. */\n"
        + entry
        + ",\n#endif\n{"
    )
    text = text[: matches[0].start()] + wrapped + text[matches[0].end() :]
    write(path, text)


def patch_usb_quirks(kernel: Path, keep_quirks: bool) -> None:
    path = kernel / "drivers/usb/core/quirks.c"
    text = read(path)
    marker = "static const struct usb_device_id usb_quirk_list[] = {\n"
    entry = (
        "\t/* ASIX AX88179A/B revision 0x0200 */\n"
        "\t{ USB_DEVICE_VER(0x0b95, 0x1790, 0x0200, 0x0200),\n"
        "\t  .driver_info = USB_QUIRK_RESET_RESUME | USB_QUIRK_NO_LPM },\n"
    )

    broad = re.compile(
        r"\s*/\*[^\n]*AX88179[^\n]*\*/\s*\n?\s*\{\s*USB_DEVICE\(0x0b95,\s*0x1790\).*?\},\s*\n?",
        re.DOTALL | re.IGNORECASE,
    )
    exact = re.compile(
        r"\s*/\* ASIX AX88179A/B revision 0x0200 \*/\s*\n"
        r"\s*\{ USB_DEVICE_VER\(0x0b95, 0x1790, 0x0200, 0x0200\),\s*\n"
        r"\s*\.driver_info = USB_QUIRK_RESET_RESUME \| USB_QUIRK_NO_LPM \},\s*\n"
    )
    text = broad.sub("\n", text)
    text = exact.sub("\n", text)
    if keep_quirks:
        if marker not in text:
            die("USB quirk table marker not found")
        text = text.replace(marker, marker + entry, 1)
    write(path, text)


def patch_asix_driver(driver: Path, pin_runtime_pm: bool) -> None:
    header_path = driver / "ax_main.h"
    header = read(header_path)
    guard = "#if LINUX_VERSION_CODE < KERNEL_VERSION(4, 10, 0)"
    if guard not in header:
        pattern = re.compile(
            r"(?m)^(#define\s+napi_complete_done\(n, d\)[^\n]*)$"
        )
        matches = list(pattern.finditer(header))
        if len(matches) != 1:
            die(
                "modern napi_complete_done compatibility: "
                f"expected one macro, found {len(matches)}"
            )
        line = matches[0].group(1)
        replacement = guard + "\n" + line + "\n#endif"
        header = pattern.sub(replacement, header, count=1)
        write(header_path, header)

    path = driver / "ax_main.c"
    text = read(path)

    exact_entry = """\t/* Exact AX88179A/B match must precede overlapping legacy ranges. */
\tASIX_USB_DEVICE(USB_VENDOR_ID_ASIX, AX_DEVICE_ID_179X,
\t\t\tAX_BCDDEVICE_ID_179A, AX_BCDDEVICE_ID_179A,
\t\t\tax88179a_info),
"""
    text = insert_before_once(
        text,
        "\tASIX_USB_DEVICE(USB_VENDOR_ID_ASIX, AX_DEVICE_ID_179X, 0,\n\t\t\tAX_BCDDEVICE_ID_179, ax88179_info),\n",
        exact_entry,
        "exact AX88179A/B ID ordering",
    )

    old_budget = """#ifndef ENABLE_TX_TASKLET
\t\tif (!ret) {
\t\t\t*work_done += desc->skb_num;
\t\t\tif (*work_done >= budget)
\t\t\t\tbreak;
\t\t}
#endif
"""
    new_budget = """#ifndef ENABLE_TX_TASKLET
\t\tif (!ret && budget > 0) {
\t\t\tint remaining = budget - *work_done;

\t\t\t/*
\t\t\t * TX completions may be processed without a budget limit, but
\t\t\t * a NAPI poll must never report more than its supplied budget.
\t\t\t */
\t\t\t*work_done += min_t(int, desc->skb_num, remaining);
\t\t\tif (*work_done == budget)
\t\t\t\tbreak;
\t\t}
#endif
"""
    text = replace_once(text, old_budget, new_budget, "TX NAPI accounting")

    old_invalid = """\tif (test_bit(AX_UNPLUG, &axdev->flags) ||
\t    !test_bit(AX_ENABLE, &axdev->flags) ||
\t    !netif_carrier_ok(axdev->netdev))
\t\treturn -1;
"""
    new_invalid = """\tif (test_bit(AX_UNPLUG, &axdev->flags) ||
\t    !test_bit(AX_ENABLE, &axdev->flags) ||
\t    !netif_carrier_ok(axdev->netdev)) {
\t\tif (budget)
\t\t\tnapi_complete_done(tx_napi, 0);
\t\treturn 0;
\t}
"""
    text = replace_once(text, old_invalid, new_invalid, "TX NAPI inactive return")

    old_complete = """\tif (work_done < budget) {
#if KERNEL_VERSION(4, 10, 0) > LINUX_VERSION_CODE
\t\tnapi_complete_done(tx_napi, 0);
#else
\t\tif (!napi_complete_done(tx_napi, 0))
\t\t\treturn 0;
#endif
"""
    new_complete = """\tif (budget && work_done < budget) {
#if KERNEL_VERSION(4, 10, 0) > LINUX_VERSION_CODE
\t\tnapi_complete_done(tx_napi, work_done);
#else
\t\tif (!napi_complete_done(tx_napi, work_done))
\t\t\treturn work_done;
#endif
"""
    text = replace_once(text, old_complete, new_complete, "TX NAPI completion")

    if pin_runtime_pm:
        helper = """
static bool ax_is_88179ab(const struct usb_device *udev)
{
\treturn le16_to_cpu(udev->descriptor.idVendor) == 0x0b95 &&
\t\tle16_to_cpu(udev->descriptor.idProduct) == 0x1790 &&
\t\tle16_to_cpu(udev->descriptor.bcdDevice) == AX_BCDDEVICE_ID_179A;
}

static void ax_pin_88179ab_runtime_pm(struct usb_device *udev)
{
\tif (!IS_ENABLED(CONFIG_USB_NET_AX_USB_NIC_PIN_RUNTIME_PM) ||
\t    !ax_is_88179ab(udev))
\t\treturn;

\tusb_disable_autosuspend(udev);
\tpm_runtime_forbid(&udev->dev);
\tdev_info(&udev->dev, "AX88179A/B runtime PM pinned active\\n");
}

"""
        text = insert_before_once(
            text,
            "static int ax_probe(struct usb_interface *intf, const struct usb_device_id *id)\n",
            helper,
            "runtime PM helper",
        )
        old_probe = """	if (udev->actconfig->desc.bConfigurationValue != 1) {
		usb_driver_set_configuration(udev, 1);
		return -ENODEV;
	}

	info = (const struct driver_info *)id->driver_info;
"""
        new_probe = """	if (udev->actconfig->desc.bConfigurationValue != 1) {
		usb_driver_set_configuration(udev, 1);
		return -ENODEV;
	}

	/* The interface probe is the guaranteed bind point for this driver. */
	ax_pin_88179ab_runtime_pm(udev);

	info = (const struct driver_info *)id->driver_info;
"""
        text = replace_once(
            text, old_probe, new_probe, "runtime PM interface probe call"
        )

    write(path, text)


def write_config_fragment(kernel: Path, pin_runtime_pm: bool) -> Path:
    output = kernel / "ax88179ab-inline.fragment"
    lines = [
        "CONFIG_USB_NET_DRIVERS=y",
        "CONFIG_MII=y",
        "CONFIG_USB_NET_AX_USB_NIC=y",
    ]
    lines.append(
        "CONFIG_USB_NET_AX_USB_NIC_PIN_RUNTIME_PM=" + ("y" if pin_runtime_pm else "n")
    )
    write(output, "\n".join(lines) + "\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--asix", required=True, type=Path)
    parser.add_argument(
        "--usb-quirks", choices=("keep-0x402", "driver-only"), default="keep-0x402"
    )
    parser.add_argument(
        "--runtime-pm", choices=("pinned", "default"), default="pinned"
    )
    args = parser.parse_args()

    kernel = args.kernel.resolve()
    asix = args.asix.resolve()
    verify_asix_source(asix)
    driver = copy_driver(kernel, asix)
    integrate_kbuild(kernel)
    patch_generic_config_choice(kernel)
    patch_legacy_driver_conflict(kernel)
    patch_usb_quirks(kernel, args.usb_quirks == "keep-0x402")
    patch_asix_driver(driver, args.runtime_pm == "pinned")
    fragment = write_config_fragment(kernel, args.runtime_pm == "pinned")

    print(f"inlined ASIX source: {driver}")
    print(f"config fragment: {fragment}")
    print(f"USB quirks: {args.usb_quirks}")
    print(f"runtime PM: {args.runtime_pm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
