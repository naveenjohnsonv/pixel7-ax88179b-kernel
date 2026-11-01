"""Exercise the ABI gate against compatible, corrupt and mismatched ELF data."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('module_abi',HERE/'module-abi.py')
abi=importlib.util.module_from_spec(spec)
spec.loader.exec_module(abi)


def elf(crc=0x12345678, absolute=False, extra=False, duplicate=False, address=0x1000):
    data=bytearray(64)
    data[:16]=b'\x7fELF\x02\x01\x01'+b'\0'*9
    names=b'\0.rodata\0.strtab\0.symtab\0.shstrtab\0'
    strings=b'\0__crc_alpha\0__crc_extra\0'
    sections=[(0,)*10]
    def add(name,kind,flags,addr,blob,link=0,align=1,entsize=0):
        off=len(data);data.extend(blob)
        sections.append((names.index(name.encode()+b'\0'),kind,flags,addr,off,len(blob),link,0,align,entsize))
    add('.rodata',1,2,0x1000,struct.pack('<I',crc),align=4)
    add('.strtab',3,0,0,strings)
    symbols=b'\0'*24+struct.pack('<IBBHQQ',1,0,0,0xfff1 if absolute else 1,crc if absolute else address,4)
    if extra: symbols+=struct.pack('<IBBHQQ',strings.index(b'__crc_extra'),0,0,0xfff1,0x11223344,0)
    if duplicate: symbols+=struct.pack('<IBBHQQ',1,0,0,0xfff1,crc,0)
    add('.symtab',2,0,0,symbols,link=2,align=8,entsize=24)
    add('.shstrtab',3,0,0,names)
    while len(data)%8:data.append(0)
    off=len(data)
    for section in sections:data.extend(struct.pack('<IIQQQQIIQQ',*section))
    struct.pack_into('<HHIQQQIHHHHHH',data,16,2,183,1,0,0,off,0,64,0,0,64,len(sections),4)
    return bytes(data)


def baseline(**exports):
    return dict(schema=1,baseline=dict(Image_sha256='a'*64),exports=exports or dict(alpha='12345678'))


class ModuleABITests(unittest.TestCase):
    def test_section_relative_CRC_matches(self):
        self.assertEqual(abi.compare(elf(),baseline())['status'],'PASS')

    def test_absolute_CRC_matches(self):
        self.assertEqual(abi.export_crcs(elf(absolute=True)),dict(alpha='12345678'))

    def test_changed_CRC_fails(self):
        report=abi.compare(elf(crc=0x12345679),baseline())
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(report['mismatched_exports'][0]['actual'],'12345679')

    def test_missing_export_fails(self):
        self.assertEqual(abi.compare(elf(),baseline(beta='12345678'))['missing_exports'],['beta'])

    def test_new_exports_do_not_remove_baseline_compatibility(self):
        report=abi.compare(elf(extra=True),baseline())
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(report['additional_exports'],['extra'])

    def test_corrupt_wrong_architecture_duplicate_and_unmapped_CRC_fail(self):
        wrong=bytearray(elf());struct.pack_into('<H',wrong,18,62)
        for data in [b'',elf()[:70],bytes(wrong),elf(duplicate=True),elf(address=0x2000)]:
            with self.subTest(data=data[:20]):
                with self.assertRaises(ValueError):abi.export_crcs(data)

    def test_failure_exit_still_preserves_structured_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'vmlinux').write_bytes(elf(crc=0))
            (root/'baseline.json').write_text(json.dumps(baseline()))
            result=subprocess.run([sys.executable,str(HERE/'module-abi.py'),'--vmlinux',str(root/'vmlinux'),
                '--baseline',str(root/'baseline.json'),'--report',str(root/'report.json')],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(json.loads((root/'report.json').read_text())['status'],'FAIL')

    def test_malformed_baseline_fails_closed(self):
        for b in [{},baseline(alpha='invalid')]:
            with self.assertRaises(ValueError):abi.compare(elf(),b)


if __name__=='__main__':unittest.main()
