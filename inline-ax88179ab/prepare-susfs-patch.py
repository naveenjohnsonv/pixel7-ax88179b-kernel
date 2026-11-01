#!/usr/bin/env python3
"""Adapt one known SUSFS namespace context difference without changing code.

Some android14-6.1 sources lack trace/hooks/blk.h in fs/namespace.c. The SUSFS
patch mentions that include only as unchanged context. Remove just that context
line, then require a complete patch dry-run in CI; never ignore rejected hunks.
"""
import argparse
from pathlib import Path
import re

CONTEXT = ' #include <trace/hooks/blk.h>\n'


def prepare(patch, namespace):
    if '#include <trace/hooks/blk.h>' in namespace.splitlines():
        return patch
    marker = 'diff --git a/fs/namespace.c b/fs/namespace.c\n'
    if patch.count(marker) != 1:
        raise ValueError('expected one fs/namespace.c patch section')
    start = patch.index(marker)
    first = patch.index('@@ ', start)
    header_end = patch.index('\n', first) + 1
    next_hunk = patch.find('\n@@ ', header_end)
    next_file = patch.find('\ndiff --git ', header_end)
    end = min(n + 1 for n in [next_hunk, next_file, len(patch) - 1] if n >= 0)
    header = patch[first:header_end]
    match = re.fullmatch(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@\n', header)
    if not match:
        raise ValueError('unrecognized first namespace hunk header')
    body = patch[header_end:end]
    if body.count(CONTEXT) != 1:
        raise ValueError('expected one unchanged blk.h context line in first namespace hunk')
    old_start, old_count, new_start, new_count = map(int, match.groups())
    before_old = [l[1:] for l in body.splitlines(keepends=True) if l.startswith((' ', '-'))]
    before_new = [l[1:] for l in body.splitlines(keepends=True) if l.startswith((' ', '+'))]
    if len(before_old) != old_count or len(before_new) != new_count:
        raise ValueError('namespace hunk counts do not match its body')
    updated_body = body.replace(CONTEXT, '', 1)
    # Verify that no added/deleted code line was removed or modified.
    changes = lambda text: [l for l in text.splitlines() if l.startswith(('+', '-'))]
    if changes(body) != changes(updated_body):
        raise ValueError('refusing to change SUSFS additions or deletions')
    updated_header = f'@@ -{old_start},{old_count - 1} +{new_start},{new_count - 1} @@\n'
    return patch[:first] + updated_header + updated_body + patch[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patch', required=True, type=Path)
    parser.add_argument('--namespace', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        original = args.patch.read_text()
        result = prepare(original, args.namespace.read_text())
        args.output.write_text(result)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    print('SUSFS patch: ' + ('unchanged' if result == original else 'one absent context line removed; code changes preserved'))


if __name__ == '__main__':
    main()
