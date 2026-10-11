"""Installed private-workspace setup, recovery, export and deletion."""

from __future__ import annotations

import argparse
import json
import sys

from career_os.infrastructure.instance_data import create_instance_data_service
from career_os.infrastructure.instances import create_instance_service
from career_os.infrastructure.recovery import create_recovery_service
from career_os.public_api import RecoveryInventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Private workspace setup, diagnostics, recovery, export and deletion."
    )
    parser.add_argument("--instance-root", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--listener-port", type=int)
    upgrade = commands.add_parser("upgrade")
    upgrade.add_argument("--backup-destination", required=True)
    upgrade.add_argument("--confirm-upgrade", metavar="PREVIEW_SHA256")
    upgrade.add_argument("--offline", action="store_true")
    commands.add_parser("inventory")
    backup = commands.add_parser("backup")
    backup.add_argument("--destination", required=True)
    backup.add_argument("--offline", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--backup", required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--confirm-restore", metavar="PREVIEW_SHA256")
    restore.add_argument("--offline", action="store_true")
    export = commands.add_parser("export")
    export.add_argument("--destination", required=True)
    export.add_argument("--offline", action="store_true")
    export.add_argument("--historical-archive", action="store_true")
    delete = commands.add_parser("delete")
    delete.add_argument("--confirm-delete", metavar="PREVIEW_SHA256")
    delete.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in {"init", "doctor", "upgrade"}:
            instances = create_instance_service(args.instance_root)
            if args.command == "init":
                result = instances.initialize(actor="local_user")
            elif args.command == "doctor":
                result = instances.doctor(listener_port=args.listener_port)
            else:
                result = instances.upgrade(
                    args.backup_destination,
                    confirmation_fingerprint=args.confirm_upgrade,
                    offline=args.offline,
                    actor="local_user" if args.confirm_upgrade else None,
                )
            print(json.dumps(result.to_dict(), sort_keys=True))
            return 0 if args.command != "doctor" or result.ready else 1
        if args.command in {"export", "delete"}:
            data = create_instance_data_service(args.instance_root)
            if args.command == "export":
                result = data.export_user_data(
                    args.destination,
                    offline=args.offline,
                    actor="local_user",
                    historical_archive=args.historical_archive,
                )
            else:
                result = data.delete(
                    confirmation_fingerprint=args.confirm_delete,
                    offline=args.offline,
                    actor="local_user" if args.confirm_delete else None,
                )
            payload = result.to_dict()
        else:
            service = create_recovery_service(args.instance_root)
            if args.command == "inventory":
                result = service.inventory()
            elif args.command == "backup":
                result = service.backup(args.destination, offline=args.offline, actor="local_user")
            elif args.command == "verify":
                result = service.verify(args.backup)
            else:
                result = service.restore(
                    args.backup,
                    confirmation_fingerprint=args.confirm_restore,
                    offline=args.offline,
                    actor="local_user" if args.confirm_restore else None,
                )
            payload = result.to_dict()
            if isinstance(result, RecoveryInventory):
                payload["backup_fingerprint"] = result.fingerprint
    except (ValueError, OSError):
        print(
            "Rolevane instance operation failed: check runtime, offline state, paths, space, integrity and exact confirmation. "
            "An incomplete export/backup/restore or partially deleted selected workspace may require local inspection.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
