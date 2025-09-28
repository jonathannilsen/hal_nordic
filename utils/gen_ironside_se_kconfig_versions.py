# Copyright (c) 2025 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from subprocess import run
from textwrap import dedent


TEMPLATE = dedent("""\
    # Copyright (c) 2025 Nordic Semiconductor ASA
    # SPDX-License-Identifier: Apache-2.0

    # This file is generated from the IronSide SE versions.h header file
    # using {script_name}.

    {named_versions}

    {latest_versions}

    config IRONSIDE_SE_VERSION_TARGET
    \thex
    {conversion_defaults}
    \tdefault 0 # Invalid/unknown version
    \thelp
    \t  The minimum version of IronSide SE targeted by the application, in hex format.
    \t  Used for compatibility and feature guarding.

    config IRONSIDE_SE_VERSION_TARGET_IS_SUPPORTED
    \tbool
    {platform_depends}
    \thelp
    \t  True if the targeted IronSide SE version is known.

    config IRONSIDE_SE_VERSION_TARGET_STRING
    \tstring
    \thelp
    \t  The minimum version of IronSide SE targeted by the application, in string format.
    \t  Used for compatibility and feature guarding.
    """)

# TODO: fix too long lines
STR_TO_HEX_DEFAULT_TEMPLATE = (
    '\tdefault $({prefix}_hex) if IRONSIDE_SE_VERSION_TARGET_STRING = "$({prefix}_str)"'
)

PLATFORM_DEPENDS_ON_TEMPLATE = (
    "\tdefault y if (IRONSIDE_SE_VERSION_TARGET = $({prefix}_hex)) && {config_depends}"
)

PLATFORM_LATEST_STRING_TEMPLATE = '\tdefault "$({prefix}_str)" if {config_depends}'

MACRO_PATTERN = (
    r"#define"
    r"[\s]+"
    r"(?P<ver>IRONSIDE_SE_V[0-9]+_[0-9]+_[0-9]+_[0-9]+)"
    r"(_(?P<prop>[^\s]+))?"
    r"[\s]+"
    r"(?P<val>[^\s]+)"
)

LATEST_MACRO_PATTERN = (
    r"#define"
    r"[\s]+"
    r"LATEST_IRONSIDE_SE_VERSION"
    r"[\s]+"
    r"(?P<ver>[^\s]+)"
)

MDK_DEFINE_TO_SOC = {
    "NRF54H20_XXAA": "SOC_NRF54H20",
    "NRF9230_ENGB_XXAA": "SOC_NRF9230_ENGB",
}


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--in-header",
        required=True,
        type=Path,
        help="Path to the ironside versions.h header file",
    )
    parser.add_argument(
        "--out-kconfig",
        type=argparse.FileType("w", encoding="utf-8"),
        default=sys.stdout,
        help="Path to write the generated Kconfig file to.",
    )
    args = parser.parse_args()

    preprocess_cmd = ["gcc", "-E", "-dM", str(args.in_header)]

    base_output = run(
        preprocess_cmd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    output_by_soc = {}

    for mdk_define in MDK_DEFINE_TO_SOC:
        proc = run(
            preprocess_cmd + [f"-D{mdk_define}"],
            capture_output=True,
            text=True,
            check=True,
        )
        output_by_soc[mdk_define] = proc.stdout

    named_version_lines = []
    conversion_default_lines = set()
    platform_depend_lines = []

    platform_depends = defaultdict(set)

    for macro_m in re.finditer(MACRO_PATTERN, base_output, flags=re.MULTILINE):
        version = macro_m["ver"]
        prefix = version.lower()
        value = macro_m["val"].strip().removeprefix('"').removesuffix('"').removesuffix("UL")
        match macro_m.group("prop"):
            case None:
                suffix = "hex"
            case "STRING":
                suffix = "str"
            case other:
                if other.startswith("SOC_") and int(value):
                    soc = other.removeprefix("SOC_")
                    platform_depends[prefix].add(MDK_DEFINE_TO_SOC[soc])
                continue

        name = f"{prefix}_{suffix}"
        named_version_lines.append(f"{name} := {value}")
        conversion_default_lines.add(STR_TO_HEX_DEFAULT_TEMPLATE.format(prefix=prefix))

    for prefix in platform_depends:
        soc_configs = sorted(platform_depends[prefix])
        if len(soc_configs) > 1:
            config_depends = f"({' || '.join(soc_configs)})"
        else:
            config_depends = soc_configs[0]
        platform_depend_lines.append(
            PLATFORM_DEPENDS_ON_TEMPLATE.format(prefix=prefix, config_depends=config_depends)
        )

    latest_version_lines = []

    for soc, soc_output in output_by_soc.items():
        latest_m = re.search(LATEST_MACRO_PATTERN, soc_output, flags=re.MULTILINE)
        if not latest_m:
            continue
        version = latest_m["ver"]
        prefix = version.lower()
        zephyr_soc = MDK_DEFINE_TO_SOC[soc]
        name = f"ironside_se_latest_{zephyr_soc.lower()}_str"
        value = f"{prefix}_str"
        latest_version_lines.append(f'{name} := $({value})')

    named_versions = "\n".join(sorted(named_version_lines))
    latest_versions = "\n".join(sorted(latest_version_lines))
    conversion_defaults = "\n".join(sorted(conversion_default_lines))
    platform_depends = "\n".join(sorted(platform_depend_lines))

    file_content = TEMPLATE.format(
        script_name=Path(__file__).name,
        named_versions=named_versions,
        latest_versions=latest_versions,
        conversion_defaults=conversion_defaults,
        platform_depends=platform_depends,
    )

    args.out_kconfig.write(file_content)


if __name__ == "__main__":
    main()
