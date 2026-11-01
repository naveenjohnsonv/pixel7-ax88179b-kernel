"""Reproduce the namespace header rejection and preserve strict patch behavior."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('susfs_prepare', HERE / 'prepare-susfs-patch.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# The actual upstream first namespace hunk; blank context lines are encoded
# below to avoid trailing whitespace in this Python source.
HUNK = '''@@ -32,11 +32,21 @@
 #include <linux/fs_context.h>
 #include <linux/shmem_fs.h>
 #include <linux/mnt_idmapping.h>
+#ifdef CONFIG_KSU_SUSFS
+#include <linux/susfs_def.h>
+#endif // #ifdef CONFIG_KSU_SUSFS

 #include "pnode.h"
 #include "internal.h"
 #include <trace/hooks/blk.h>

+#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+extern bool susfs_is_current_ksu_domain(void);
+extern struct static_key_true susfs_is_sdcard_android_data_not_decrypted;
+
+#define CL_COPY_MNT_NS BIT(25) /* used by copy_mnt_ns() */
+#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
+
 /* Maximum number of mounts in a mount namespace */
 static unsigned int sysctl_mount_max __read_mostly = 100000;

'''.replace('\n\n', '\n \n')
PATCH = ('diff --git a/fs/namespace.c b/fs/namespace.c\n'
         '--- a/fs/namespace.c\n+++ b/fs/namespace.c\n' + HUNK)
SOURCE = ''.join(l[1:] for l in HUNK.splitlines(keepends=True) if l.startswith(' '))


class SusfsPatchTests(unittest.TestCase):
    def apply(self, patch, source):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'fs').mkdir()
            target = root / 'fs/namespace.c'
            target.write_text(source)
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1'], cwd=root,
                                    input=patch, text=True, capture_output=True)
            return result.returncode, target.read_text(), result.stdout + result.stderr

    def test_reproduce_rejection_and_apply_context_only_fix(self):
        source = SOURCE.replace('#include <trace/hooks/blk.h>\n', '')
        code, _, _ = self.apply(PATCH, source)
        self.assertNotEqual(code, 0)
        prepared = module.prepare(PATCH, source)
        code, final, output = self.apply(prepared, source)
        self.assertEqual(code, 0, output)
        expected = ''.join(l[1:] for l in HUNK.splitlines(keepends=True) if l.startswith((' ', '+')))
        expected = expected.replace('#include <trace/hooks/blk.h>\n', '')
        self.assertEqual(final, expected)

    def test_existing_include_needs_no_change(self):
        self.assertEqual(module.prepare(PATCH, SOURCE), PATCH)
        self.assertEqual(self.apply(PATCH, SOURCE)[0], 0)

    def test_other_rejections_remain_failures(self):
        source = SOURCE.replace('#include <trace/hooks/blk.h>\n', '').replace('#include <linux/shmem_fs.h>\n', '')
        self.assertNotEqual(self.apply(module.prepare(PATCH, source), source)[0], 0)

    def test_unknown_hunk_is_rejected(self):
        source = SOURCE.replace('#include <trace/hooks/blk.h>\n', '')
        for text in [PATCH.replace(' #include <trace/hooks/blk.h>', '+#include <trace/hooks/blk.h>'),
                     PATCH.replace('-32,11', '-32,99'), PATCH.replace('a/fs/namespace.c b/fs/namespace.c', 'a/fs/other.c b/fs/other.c')]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    module.prepare(text, source)


if __name__ == '__main__':
    unittest.main()
