from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skill_debugger.store import WorkspaceStore


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = WorkspaceStore(Path(self.tempdir.name) / "workspaces")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_create_workspace_falls_back_to_workspace_id_for_blank_name(self) -> None:
        workspace = self.store.create_workspace("   ")

        self.assertTrue(workspace["workspace_id"])
        self.assertEqual(workspace["name"], workspace["workspace_id"])

    def test_list_workspaces_skips_corrupt_workspace_metadata(self) -> None:
        healthy = self.store.create_workspace("healthy")
        broken_dir = self.store.workspace_dir("broken")
        broken_dir.mkdir(parents=True, exist_ok=True)
        (broken_dir / "workspace.json").write_text("{not-json", encoding="utf-8")

        workspaces = self.store.list_workspaces()

        self.assertEqual([item["workspace_id"] for item in workspaces], [healthy["workspace_id"]])

    def test_write_skill_package_preserves_previous_version_on_write_failure(self) -> None:
        workspace = self.store.create_workspace("rollback")
        workspace_id = workspace["workspace_id"]
        self.store.write_skill_package(
            workspace_id,
            "demo-skill",
            {
                "SKILL.md": b"old-skill",
                "notes.txt": b"old-notes",
            },
        )
        original_package = self.store.read_skill_package(workspace_id, "demo-skill")
        real_write_bytes = Path.write_bytes

        def flaky_write_bytes(path: Path, data: bytes) -> int:
            if data == b"trigger-failure":
                raise OSError("simulated disk write failure")
            return real_write_bytes(path, data)

        with mock.patch("pathlib.Path.write_bytes", new=flaky_write_bytes):
            with self.assertRaises(OSError):
                self.store.write_skill_package(
                    workspace_id,
                    "demo-skill",
                    {
                        "SKILL.md": b"new-skill",
                        "notes.txt": b"trigger-failure",
                    },
                )

        self.assertEqual(self.store.read_skill_package(workspace_id, "demo-skill"), original_package)


if __name__ == "__main__":
    unittest.main()

