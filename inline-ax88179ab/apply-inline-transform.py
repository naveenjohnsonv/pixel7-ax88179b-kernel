#!/usr/bin/env python3
"""Apply the complete inline AX88179B Pixel kernel transformation.

The transform is strict and idempotent. It deliberately stays inside the
network-driver subtree: the ASIX driver already contains the USB device-level
configuration selector needed to move AX88179A/B hardware to configuration 1.
It also applies the target Exynos xHCI quirk and KernelSU credential fix.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import NoReturn

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
MII_HELPERS = (
    "mii_link_ok",
    "mii_nway_restart",
    "mii_ethtool_gset",
    "mii_ethtool_get_link_ksettings",
    "mii_ethtool_sset",
    "mii_ethtool_set_link_ksettings",
    "mii_check_link",
    "mii_check_media",
    "mii_check_gmii_support",
    "generic_mii_ioctl",
)


def die(message: str) -> NoReturn:
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


def verify_asix_source(asix: Path) -> None:
    for name in ASIX_FILES:
        if not (asix / name).is_file():
            die(f"ASIX source is incomplete: missing {name}")

    header = read(asix / "ax_main.h")
    if 'DRIVER_VERSION' not in header or '"4.1.0"' not in header:
        die("the checked-out ASIX source is not ax_usb_nic 4.1.0")


def copy_driver(kernel: Path, asix: Path) -> Path:
    destination = kernel / "drivers/net/usb/ax_usb_nic"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ASIX_FILES:
        shutil.copy2(asix / name, destination / name)

    write(
        destination / "Makefile",
        "# SPDX-License-Identifier: GPL-2.0\n"
        "obj-$(CONFIG_USB_NET_AX_USB_NIC) += ax_usb_nic.o\n"
        "ax_usb_nic-y := ax_main.o ax88179_178a.o ax88179a_772d.o "
        "ax_ptp.o ax_mii_compat.o\n",
    )
    add_private_mii_helpers(kernel, destination)
    return destination


def add_private_mii_helpers(kernel: Path, driver: Path) -> None:
    """Keep CONFIG_MII=m while satisfying the built-in driver's MII calls."""
    header = """/* SPDX-License-Identifier: GPL-2.0 */
#ifndef __AX_MII_COMPAT_H
#define __AX_MII_COMPAT_H

#include <linux/ethtool.h>
#include <linux/mii.h>

int ax_mii_link_ok(struct mii_if_info *mii);
int ax_mii_nway_restart(struct mii_if_info *mii);
void ax_mii_ethtool_gset(struct mii_if_info *mii, struct ethtool_cmd *ecmd);
void ax_mii_ethtool_get_link_ksettings(
\tstruct mii_if_info *mii, struct ethtool_link_ksettings *cmd);
int ax_mii_ethtool_sset(struct mii_if_info *mii, struct ethtool_cmd *ecmd);
int ax_mii_ethtool_set_link_ksettings(
\tstruct mii_if_info *mii, const struct ethtool_link_ksettings *cmd);
int ax_mii_check_gmii_support(struct mii_if_info *mii);
void ax_mii_check_link(struct mii_if_info *mii);
unsigned int ax_mii_check_media(struct mii_if_info *mii,
\t\t\t\tunsigned int ok_to_print,
\t\t\t\tunsigned int init_media);
int ax_generic_mii_ioctl(struct mii_if_info *mii_if,
\t\t\t struct mii_ioctl_data *mii_data, int cmd,
\t\t\t unsigned int *duplex_changed);

#endif
"""
    write(driver / "ax_mii_compat.h", header)

    source = read(kernel / "drivers/net/mii.c")
    for name in MII_HELPERS:
        source = re.sub(rf"\b{re.escape(name)}\b", "ax_" + name, source)
    source = re.sub(
        r"(?m)^MODULE_(?:AUTHOR|DESCRIPTION|LICENSE)\s*\(.*\);\n?", "", source
    )
    source = re.sub(r"(?m)^EXPORT_SYMBOL\([^\n]+\);\n?", "", source)
    source = (
        "/* Private MII helpers for built-in ax_usb_nic; generated from "
        "drivers/net/mii.c. */\n"
        '#include "ax_mii_compat.h"\n\n'
        + source
    )
    write(driver / "ax_mii_compat.c", source)

    for name in ASIX_FILES:
        path = driver / name
        if path.suffix not in (".c", ".h"):
            continue
        text = read(path)
        for helper in MII_HELPERS:
            text = re.sub(rf"\b{re.escape(helper)}\b", "ax_" + helper, text)
        write(path, text)

    main_header = driver / "ax_main.h"
    text = read(main_header)
    include = '#include "ax_mii_compat.h"\n'
    anchor = "#include <linux/ethtool.h>\n"
    if include not in text:
        if text.count(anchor) != 1:
            die("ASIX header: ethtool include anchor not found once")
        text = text.replace(anchor, anchor + include, 1)
        write(main_header, text)


def integrate_kbuild(kernel: Path) -> None:
    kconfig_path = kernel / "drivers/net/usb/Kconfig"
    kconfig = read(kconfig_path)
    block = """config USB_NET_AX_USB_NIC
\tbool "ASIX ax_usb_nic 4.1.0 vendor driver"
\thelp
\t  Build the official ASIX USB Ethernet driver directly into the Image.
\t  The driver contains its own device-level configuration selector, so
\t  AX88179A/B devices use vendor configuration 1 without a USB-core patch.
\t  Private copies of the required MII helpers keep the GKI mii.ko layout
\t  unchanged.

"""
    if "config USB_NET_AX_USB_NIC\n" in kconfig:
        pattern = re.compile(
            r"config USB_NET_AX_USB_NIC\n.*?(?=endif # USB_NET_DRIVERS\n)",
            re.DOTALL,
        )
        matches = list(pattern.finditer(kconfig))
        if len(matches) != 1:
            die(f"USB network Kconfig: expected one ASIX block, found {len(matches)}")
        kconfig = pattern.sub(block, kconfig, count=1)
        write(kconfig_path, kconfig)
    else:
        marker = "endif # USB_NET_DRIVERS\n"
        if kconfig.count(marker) != 1:
            die("USB network Kconfig: expected one closing marker")
        kconfig = kconfig.replace(marker, block + marker, 1)
        write(kconfig_path, kconfig)

    makefile_path = kernel / "drivers/net/usb/Makefile"
    makefile = read(makefile_path)
    line = "obj-$(CONFIG_USB_NET_AX_USB_NIC)\t+= ax_usb_nic/\n"
    if line not in makefile:
        anchor = "obj-$(CONFIG_USB_NET_AX88179_178A)      += ax88179_178a.o\n"
        if makefile.count(anchor) != 1:
            die("USB network Makefile: stock AX88179 anchor not found once")
        makefile = makefile.replace(anchor, line + anchor, 1)
        write(makefile_path, makefile)


def patch_legacy_driver_conflict(kernel: Path) -> None:
    path = kernel / "drivers/net/usb/ax88179_178a.c"
    text = read(path)
    if "AX88179 handled by built-in ax_usb_nic" in text:
        return

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
        "\t/* AX88179 handled by built-in ax_usb_nic. */\n\t"
        + entry
        + ",\n#endif\n{"
    )
    text = text[: matches[0].start()] + wrapped + text[matches[0].end() :]
    write(path, text)


def patch_asix_driver(driver: Path) -> None:
    header_path = driver / "ax_main.h"
    header = read(header_path)
    guard = "#if LINUX_VERSION_CODE < KERNEL_VERSION(4, 10, 0)"
    if guard not in header:
        pattern = re.compile(r"(?m)^(#define\s+napi_complete_done\(n, d\)[^\n]*)$")
        matches = list(pattern.finditer(header))
        if len(matches) != 1:
            die(
                "modern napi_complete_done compatibility: "
                f"expected one macro, found {len(matches)}"
            )
        line = matches[0].group(1)
        header = pattern.sub(guard + "\n" + line + "\n#endif", header, count=1)
        write(header_path, header)

    path = driver / "ax_main.c"
    text = read(path)

    exact_id = """\tASIX_USB_DEVICE(USB_VENDOR_ID_ASIX, AX_DEVICE_ID_179X, 0,
\t\t\tAX_BCDDEVICE_ID_179A, ax88179a_info),
"""
    if text.count(exact_id) != 1:
        die("ASIX 4.1.0 exact AX88179A/B ID is missing or duplicated")

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

\t\t\t/* A NAPI poll must never report more than its budget. */
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

    old_init = """\tret = usb_register_device_driver(&ax_usb_config_driver, THIS_MODULE);
\tif (ret)
\t\treturn ret;
\treturn usb_register(&ax_usb_driver);
"""
    new_init = """\tret = usb_register_device_driver(&ax_usb_config_driver, THIS_MODULE);
\tif (ret)
\t\treturn ret;

\tret = usb_register(&ax_usb_driver);
\tif (ret)
\t\tusb_deregister_device_driver(&ax_usb_config_driver);

\treturn ret;
"""
    text = replace_once(text, old_init, new_init, "USB driver registration unwind")
    write(path, text)


def apply_xhci_no_soft_retry(kernel: Path) -> None:
    path = kernel / "drivers/usb/host/xhci.c"
    text = read(path)
    block = """\tif (hcd->self.controller &&
\t    !strncmp(dev_name(hcd->self.controller),
\t\t     \"xhci-hcd-exynos.\",
\t\t     sizeof(\"xhci-hcd-exynos.\") - 1))
\t\txhci->quirks |= XHCI_NO_SOFT_RETRY;

"""
    if block in text:
        return

    anchor = "\tget_quirks(dev, xhci);\n\n"
    if text.count(anchor) != 1:
        die("xHCI setup anchor was not found exactly once")
    write(path, text.replace(anchor, anchor + block, 1))


def fix_kernelsu_cred_reference(ksu: Path) -> None:
    path = ksu / "kernel/feature/sucompat.c"
    text = read(path)
    vulnerable = "is_init(get_current_cred())"
    fixed = "is_init(current_cred())"
    if vulnerable in text:
        if text.count(vulnerable) != 1:
            die("KernelSU credential pattern occurred more than once")
        write(path, text.replace(vulnerable, fixed, 1))
    elif fixed not in text:
        die("KernelSU execveat credential check was not recognized")


def write_config_fragment(kernel: Path) -> Path:
    output = kernel / "ax88179ab-inline.fragment"
    write(
        output,
        "CONFIG_USB_NET_DRIVERS=y\n"
        "CONFIG_MII=m\n"
        "CONFIG_USB_NET_AX_USB_NIC=y\n",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--asix", required=True, type=Path)
    parser.add_argument("--ksu", required=True, type=Path)
    args = parser.parse_args()

    kernel = args.kernel.resolve()
    asix = args.asix.resolve()
    ksu = args.ksu.resolve()
    verify_asix_source(asix)
    driver = copy_driver(kernel, asix)
    integrate_kbuild(kernel)
    patch_legacy_driver_conflict(kernel)
    patch_asix_driver(driver)
    apply_xhci_no_soft_retry(kernel)
    fix_kernelsu_cred_reference(ksu)
    fragment = write_config_fragment(kernel)

    print(f"inlined ASIX source: {driver}")
    print(f"config fragment: {fragment}")
    print("applied Exynos XHCI_NO_SOFT_RETRY and KernelSU credential fix")
    return 0


if __name__ == "__main__":
    sys.exit(main())
