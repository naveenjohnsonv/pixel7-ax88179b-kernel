#!/usr/bin/env python3
"""Reject kernels that change the working GKI export CRC contract.

Reads ELF and a public export baseline only. Does not alter kernel/module
bytes, force loading, install anything or perform device operations.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


def cstring(data, offset):
    if not 0 <= offset < len(data):
        raise ValueError('ELF string offset outside its section')
    end = data.find(b'\0', offset)
    if end < 0:
        raise ValueError('unterminated ELF string')
    return data[offset:end].decode()


def export_crcs(data):
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01':
        raise ValueError('expected little-endian ELF64 vmlinux')
    if struct.unpack_from('<H', data, 18)[0] != 183:
        raise ValueError('expected AArch64 vmlinux')
    off = struct.unpack_from('<Q', data, 40)[0]
    size, count, names_index = struct.unpack_from('<HHH', data, 58)
    if size != 64 or not count or names_index >= count or off + size*count > len(data):
        raise ValueError('invalid ELF section table')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, off+i*size) for i in range(count)]
    for s in sections:
        if s[1] != 8 and s[4]+s[5] > len(data):
            raise ValueError('ELF section outside the file')

    def blob(section):
        return data[section[4]:section[4]+section[5]]

    def crc_at(address):
        for s in sections:
            if s[1] != 8 and s[2] & 2 and s[3] <= address and address+4 <= s[3]+s[5]:
                return struct.unpack_from('<I', data, s[4]+address-s[3])[0]
        raise ValueError(f'CRC address outside allocated file-backed sections: {address:x}')

    exports = {}
    for s in sections:
        if s[1] != 2:
            continue
        if s[9] != 24 or s[5] % 24 or s[6] >= count:
            raise ValueError('invalid ELF symbol table')
        strings = blob(sections[s[6]])
        for pos in range(s[4], s[4]+s[5], 24):
            name, _, _, index, value, _ = struct.unpack_from('<IBBHQQ', data, pos)
            name = cstring(strings, name)
            if not name.startswith('__crc_'):
                continue
            name = name[6:]
            if name in exports:
                raise ValueError('duplicate export CRC symbol: '+name)
            crc = value if index == 0xfff1 else crc_at(value)
            if not 0 <= crc <= 0xffffffff:
                raise ValueError('CRC is not an unsigned 32-bit value: '+name)
            exports[name] = f'{crc:08x}'
    if not exports:
        raise ValueError('no export CRCs found in vmlinux')
    return exports


def compare(data, baseline):
    if baseline.get('schema') != 1 or not baseline.get('exports'):
        raise ValueError('invalid or empty export baseline')
    for name, crc in baseline['exports'].items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9.$]*', name) or not re.fullmatch(r'[0-9a-f]{8}', crc):
            raise ValueError('invalid baseline export entry')
    actual = export_crcs(data)
    expected = baseline['exports']
    missing = sorted(expected.keys()-actual.keys())
    mismatches = [dict(symbol=k, expected=expected[k], actual=actual[k])
                  for k in sorted(expected.keys() & actual.keys()) if expected[k] != actual[k]]
    return dict(status='FAIL' if missing or mismatches else 'PASS',
                baseline_Image_sha256=baseline['baseline']['Image_sha256'],
                candidate_vmlinux_sha256=hashlib.sha256(data).hexdigest(),
                required_exports=len(expected), candidate_exports=len(actual),
                matched_exports=len(expected)-len(missing)-len(mismatches),
                missing_exports=missing, mismatched_exports=mismatches,
                additional_exports=sorted(actual.keys()-expected.keys()),
                enforcement='reject_mismatch_no_CRC_rewriting_or_forced_loading')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vmlinux',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    try:
        report=compare(args.vmlinux.read_bytes(),json.loads(args.baseline.read_text()))
    except (OSError,ValueError,KeyError,struct.error) as error:
        report=dict(status='ERROR',reason=str(error))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    if report['status']=='ERROR':
        parser.exit(1,'module ABI check error: '+report['reason']+'\n')
    print(f'module ABI {report["status"]}: {report["matched_exports"]}/{report["required_exports"]} '
          f'matched, {len(report["missing_exports"])} missing, {len(report["mismatched_exports"])} mismatched')
    for item in report['mismatched_exports'][:8]:
        print(f'  {item["symbol"]}: expected {item["expected"]}, actual {item["actual"]}')
    for name in report['missing_exports'][:8]:
        print('  missing: '+name)
    return report['status']!='PASS'


if __name__=='__main__':
    raise SystemExit(main())
