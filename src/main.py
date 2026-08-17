from __future__ import annotations

import argparse
import logging
import sys
import time

from src.config import Settings
from src.confluence_client import ConfluenceClient
from src.openwebui_client import OpenWebUIClient
from src.state import StateManager
from src.sync import SyncOrchestrator


def main() -> None:
    args = _parse_args()

    settings = Settings()  # type: ignore[call-arg]
    _setup_logging(settings.log_level)

    logger = logging.getLogger("src")
    logger.info("Confluence → OpenWebUI Sync starting")

    start = time.monotonic()

    state = StateManager(settings.database_url)
    state.ensure_schema()

    service_user_id = state.get_service_user_id()
    logger.info("Using OpenWebUI service user: %s", service_user_id)

    confluence = ConfluenceClient(
        base_url=settings.confluence_base_url,
        pat=settings.confluence_pat,
        timeout=settings.http_timeout,
        retries=settings.http_retries,
    )
    openwebui = OpenWebUIClient(
        base_url=settings.openwebui_base_url,
        api_key=settings.openwebui_api_key,
        service_user_id=service_user_id,
        timeout=settings.http_timeout,
        retries=settings.http_retries,
    )

    try:
        openwebui.verify_service_user()

        orchestrator = SyncOrchestrator(
            confluence=confluence,
            openwebui=openwebui,
            state=state,
            confluence_base_url=settings.confluence_base_url,
            dry_run=args.dry_run,
            force_reupload=args.force_reupload,
        )
        orchestrator.run(
            force_full=args.full or args.force_reupload,
            mapping_ids=args.mapping_ids,
        )
    except Exception:
        logger.exception("Sync failed with unhandled error")
        sys.exit(1)
    finally:
        confluence.close()
        openwebui.close()
        state.close()

    elapsed = time.monotonic() - start
    logger.info("Total duration: %.1fs", elapsed)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronize Confluence pages to OpenWebUI Knowledge Bases",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        help="Force full sync (re-sync all pages, detect orphans)",
    )
    mode.add_argument(
        "--incremental",
        action="store_true",
        help="Force incremental sync (only changed pages since last run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making changes to OpenWebUI or state DB",
    )
    parser.add_argument(
        "--force-reupload",
        action="store_true",
        help="Force re-upload of all pages (ignores version/hash checks, useful to fix metadata)",
    )
    parser.add_argument(
        "--mapping-ids",
        type=int,
        nargs="+",
        metavar="ID",
        help="Only process the given sync_mapping IDs (default: all active mappings)",
    )
    return parser.parse_args()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


if __name__ == "__main__":
    main()
