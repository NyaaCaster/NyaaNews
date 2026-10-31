from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import rebuild
import restart


class DeploymentTests(unittest.TestCase):
    def test_registry_cleanup_protects_shared_manifest(self):
        digests={'latest':'new','abcdef0':'new','shared':'new','old':'old'}
        plan=rebuild.cleanup_plan(list(digests),{'latest','abcdef0'},digests.__getitem__)
        self.assertEqual(plan,{'old':'old'})

    def test_missing_kept_manifest_prevents_any_cleanup(self):
        with self.assertRaises(ValueError):
            rebuild.cleanup_plan(['latest','old'],{'latest','abcdef0'},lambda tag:'' if tag=='abcdef0' else 'digest')

    def test_missing_mount_or_outside_directory_blocks_restart(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);mount=root/'mount';mount.mkdir()
            data=mount/'data';data.mkdir();backup=mount/'backups';backup.mkdir()
            values={'STORAGE_MOUNT_ROOT':str(mount),'HOST_DATA_DIR':str(data),'HOST_BACKUP_DIR':str(backup)}
            with patch('restart.os.path.ismount',return_value=False):
                with self.assertRaises(ValueError):restart.validate_storage(values)
            with patch('restart.os.path.ismount',return_value=True):
                restart.validate_storage(values)
                values['HOST_BACKUP_DIR']=str(root)
                with self.assertRaises(ValueError):restart.validate_storage(values)

    def test_registry_output_is_masked(self):
        self.assertEqual(rebuild.mask('http://registry.example.invalid:5000/v2/',
                         ['registry.example.invalid:5000','http://registry.example.invalid:5000']),
                         '<PRIVATE_REGISTRY>/v2/')
