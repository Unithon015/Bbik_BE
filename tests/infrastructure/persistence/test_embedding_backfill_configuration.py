import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from src import config


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "backfill_namu_wiki_incident_embeddings.py"


def load_backfill_module():
    spec = importlib.util.spec_from_file_location("backfill_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class BackfillConfigurationTest(unittest.TestCase):
    def test_backfill_uses_application_open_api_key_configuration(self):
        module = load_backfill_module()
        self.assertIs(module.config, config)

    def test_empty_application_key_stops_before_backfill_work(self):
        module = load_backfill_module()
        with (
            patch.object(module.config, "OPEN_API_KEY", ""),
            patch.object(module, "backfill_missing_incident_embeddings", new=AsyncMock()) as backfill,
        ):
            with self.assertRaisesRegex(RuntimeError, "OPEN_API_KEY"):
                module.backfill_missing_embeddings()
        backfill.assert_not_called()

    def test_sync_wrapper_delegates_to_async_backfill_service(self):
        module = load_backfill_module()
        with (
            patch.object(module.config, "OPEN_API_KEY", "test-key"),
            patch.object(
                module,
                "backfill_missing_incident_embeddings",
                new=AsyncMock(return_value=7),
            ) as backfill,
        ):
            updated = module.backfill_missing_embeddings(batch_size=32)

        self.assertEqual(updated, 7)
        backfill.assert_awaited_once_with(batch_size=32, api_key="test-key")
