"""HOST MODEL + SOURCE GATES ONLY; no device or target cache/root-cause proof.
Native host compilation is unavailable (bundled esp-clang has only ESP targets).
The separate IDF syntax check validates the actual SDK ABI and source.
"""
import pathlib
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'main/hal/mosaico/vfs_internal_ops.c').read_text()
PATH = '/dev/tusb_cdc'
STATIC = 8
OK, ARG, STATE, MEMORY = 0, 1, 2, 3


class ShimModel:
    def __init__(self):
        self.saved = None
        self.registering = self.oom = False
        self.allocations = self.frees = 0
        self.calls = []
        self.real_result = OK
        self.reenter = False
        self.reentrant_result = None

    def real(self, path, ops, flags, ctx):
        self.calls.append((path, ops, flags, ctx))
        if self.reenter:
            self.reenter = False
            self.reentrant_result = self.register(path, dict(write=99), flags, ctx)
        return self.real_result

    def register(self, path, ops, flags, ctx):
        if not flags & STATIC or path != PATH:
            return self.real(path, ops, flags, ctx)
        if ops is None or any(ops.get(nested) is not None for nested in ['dir', 'termios', 'select']):
            return ARG
        if self.registering:
            return STATE
        self.registering = True
        if self.saved is not None:
            self.registering = False
            return STATE
        if self.oom:
            self.registering = False
            return MEMORY
        self.allocations += 1
        copied = dict(ops)
        result = self.real(path, copied, flags, ctx)
        if result == OK:
            self.saved = copied
        else:
            self.frees += 1
        self.registering = False
        return result


class VfsInternalOpsTests(unittest.TestCase):
    def setUp(self):
        self.shim = ShimModel()
        self.ops = dict(write=42)
        self.ctx = object()
        self.flags = STATIC | 16

    def test_other_path_nonstatic_and_null_path_passthrough(self):
        self.shim.real_result = 7
        for path, flags in [(PATH + '-other', self.flags), (PATH, 16), (None, self.flags)]:
            self.assertEqual(self.shim.register(path, self.ops, flags, self.ctx), 7)
            call = self.shim.calls[-1]
            self.assertEqual((call[0], call[2]), (path, flags))
            self.assertIs(call[1], self.ops)
            self.assertIs(call[3], self.ctx)
        self.assertEqual(self.shim.allocations, 0)

    def test_null_and_nested_ops_no_copy(self):
        for ops in [None] + [dict(write=42, **{nested: object()}) for nested in ['dir', 'termios', 'select']]:
            self.assertEqual(self.shim.register(PATH, ops, self.flags, self.ctx), ARG)
        self.assertEqual((len(self.shim.calls), self.shim.allocations), (0, 0))

    def test_internal_copy_identity_lifetime_and_no_overwrite(self):
        self.assertEqual(self.shim.register(PATH, self.ops, self.flags, self.ctx), OK)
        self.assertIsNot(self.shim.saved, self.ops)
        self.assertEqual(self.shim.saved, self.ops)
        self.assertEqual(self.shim.calls[0][2], self.flags)
        self.assertIs(self.shim.calls[0][3], self.ctx)
        self.assertEqual(self.shim.register(PATH, dict(write=99), self.flags, self.ctx), STATE)
        self.assertEqual(self.shim.saved['write'], 42)
        self.assertEqual((len(self.shim.calls), self.shim.allocations, self.shim.frees), (1, 1, 0))

    def test_registration_failure_frees_and_allows_retry(self):
        self.shim.real_result = 9
        self.assertEqual(self.shim.register(PATH, self.ops, self.flags, self.ctx), 9)
        self.assertIsNone(self.shim.saved)
        self.assertEqual(self.shim.frees, 1)
        self.shim.real_result = OK
        self.assertEqual(self.shim.register(PATH, dict(write=99), self.flags, self.ctx), OK)
        self.assertEqual((self.shim.saved['write'], self.shim.allocations, self.shim.frees), (99, 2, 1))

    def test_reentrant_registration_exclusion(self):
        self.shim.reenter = True
        self.assertEqual(self.shim.register(PATH, self.ops, self.flags, self.ctx), OK)
        self.assertEqual(self.shim.reentrant_result, STATE)
        self.assertEqual((len(self.shim.calls), self.shim.allocations), (1, 1))

    def test_allocation_failure_unlocks_for_retry(self):
        self.shim.oom = True
        self.assertEqual(self.shim.register(PATH, self.ops, self.flags, self.ctx), MEMORY)
        self.assertFalse(self.shim.registering)
        self.assertFalse(self.shim.calls)
        self.shim.oom = False
        self.assertEqual(self.shim.register(PATH, self.ops, self.flags, self.ctx), OK)

    def test_actual_source_matches_model_gates_and_real_sdk_path(self):
        header = ROOT / 'boards/mosaico/managed_components/espressif__esp_tinyusb/include/vfs_tinyusb.h'
        self.assertIn('#define VFS_TUSB_PATH_DEFAULT "/dev/tusb_cdc"', header.read_text())
        self.assertIn('!CONFIG_IDF_TARGET_ARCH_RISCV', SOURCE)
        self.assertIn('if (!(flags & ESP_VFS_FLAG_STATIC) || !base_path || strcmp(base_path, VFS_TUSB_PATH_DEFAULT))', SOURCE)
        self.assertIn('return __real_esp_vfs_register_fs(base_path, vfs, flags, ctx);', SOURCE)
        for field in ['dir', 'termios', 'select']:
            self.assertIn('if (vfs->' + field + ') return ESP_ERR_INVALID_ARG;', SOURCE)
        self.assertLess(SOURCE.index('atomic_flag_test_and_set'), SOURCE.index('heap_caps_malloc('))
        self.assertLess(SOURCE.index('if (internal_ops)'), SOURCE.index('memcpy(ops, vfs,'))
        self.assertIn('MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT', SOURCE)
        self.assertIn('__real_esp_vfs_register_fs(base_path, ops, flags, ctx)', SOURCE)
        self.assertIn('if (result == ESP_OK) internal_ops = ops;', SOURCE)
        self.assertIn('else heap_caps_free(ops);', SOURCE)
        self.assertNotIn('internal_ops = *vfs', SOURCE)


if __name__ == '__main__':
    print('HOST MODEL/SOURCE ONLY: no real SDK execution, target cache validation, or root-cause claim.')
    unittest.main(verbosity=2)