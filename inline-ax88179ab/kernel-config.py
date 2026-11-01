#!/usr/bin/env python3
"""Apply requested config entries or verify their resolved values.

Applying to gki_defconfig is only input preparation. CI must also verify the
configuration extracted from the compiled Image after Kconfig resolution.
"""
import argparse
from pathlib import Path
import re
import sys

ENTRY = re.compile(r'(CONFIG_[A-Z0-9_]+)=(y|m|n)')
DISABLED = re.compile(r'# (CONFIG_[A-Z0-9_]+) is not set')


def requested(path):
    values = {}
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = ENTRY.fullmatch(line)
        if not match or match[1] in values:
            raise ValueError(f'{path}:{number}: invalid or duplicate request: {line}')
        values[match[1]] = match[2]
    if not values:
        raise ValueError(f'{path}: empty fragment')
    return values


def configure(mode, fragment, config):
    wanted = requested(fragment)
    lines = config.read_text().splitlines()
    if mode == 'apply':
        seen, output = set(), []
        for line in lines:
            match = ENTRY.fullmatch(line) or DISABLED.fullmatch(line)
            key = match[1] if match else None
            if key in wanted:
                if key not in seen:
                    value = wanted[key]
                    output.append(f'# {key} is not set' if value == 'n' else f'{key}={value}')
                    seen.add(key)
            else:
                output.append(line)
        for key, value in wanted.items():
            if key not in seen:
                output.append(f'# {key} is not set' if value == 'n' else f'{key}={value}')
        config.write_text('\n'.join(output) + '\n')
    else:
        effective = {key: [] for key in wanted}
        for line in lines:
            match = ENTRY.fullmatch(line)
            disabled = DISABLED.fullmatch(line)
            if match and match[1] in effective:
                effective[match[1]].append(match[2])
            elif disabled and disabled[1] in effective:
                effective[disabled[1]].append('n')
        # Kconfig omits dependent symbols when their parent is disabled.
        # An n request accepts that absence, while any y/m value or duplicate
        # still fails. Positive requirements must always be explicitly present.
        failures = [f'{key}: expected {value}, found {effective[key] or "missing"}'
                    for key, value in wanted.items()
                    if effective[key] != [value] and not (value == 'n' and not effective[key])]
        if failures:
            raise ValueError('configuration requirements failed:\n' + '\n'.join(failures))
    print(f'{mode}: {len(wanted)} requirements passed in {config}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['apply', 'verify'])
    parser.add_argument('--fragment', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        configure(args.mode, args.fragment, args.config)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
