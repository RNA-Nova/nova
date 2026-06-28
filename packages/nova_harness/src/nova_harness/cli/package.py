"""CLI entry for the Nova package manager.

Usage::

    nova-pkg list [--kind agent|tool|bundle] [--flat]
    nova-pkg install <source> [--kind <agent|tool|bundle>] [--name <name>] [-l] [--no-deps]
    nova-pkg uninstall <name> --kind <agent|tool|bundle>
    nova-pkg update <name> --kind <agent|tool|bundle>
    nova-pkg info <name> --kind <agent|tool|bundle>
    nova-pkg validate <source> [--kind <agent|tool|bundle>]
"""

import argparse
import sys

from nova_harness.core.config.defaults import CONFIG_DIR_NAME
from nova_harness.core.package import PackageManager
from nova_harness.core.package.scaffold import scaffold_package_json


def _fmt_pkg(pkg):
    return f"  [{pkg.kind:12}] {pkg.name:20} {pkg.version:10}  {pkg.source or pkg.description or ''}"


def _render_bundle_view(views):
    if not views:
        print("No packages installed.")
        return
    for name, view in views.items():
        ver = f" @{view.version}" if view.version else ""
        print(f"\nBundle: {name}{ver}")
        if view.description:
            print(f"  {view.description}")
        agents = ", ".join(a.name for a in view.agents) or "(none)"
        tools = ", ".join(t.name for t in view.tools) or "(none)"
        print(f"  Agents: {agents}")
        print(f"  Tools:  {tools}")


def cmd_list(pm: PackageManager, args):
    if args.flat:
        kind = args.kind
        pkgs = pm.list(kind=kind)
        if not pkgs:
            print("No packages installed.")
            return 0
        header = f"  {'Kind':12} {'Name':20} {'Version':10}  Source / Description"
        print(header)
        print("  " + "-" * len(header))
        for pkg in pkgs:
            print(_fmt_pkg(pkg))
        return 0

    views = pm.list_by_bundle()
    if not views:
        print("No packages installed.")
        return 0
    _render_bundle_view(views)
    return 0


def cmd_install(pm: PackageManager, args):
    meta = pm.install(
        args.source,
        kind=args.kind,
        name=args.name,
        no_deps=args.no_deps,
        with_binaries=args.with_binaries,
    )
    print(f"Installed '{meta.name}' ({meta.kind}) @ {meta.version}")
    print(f"  source: {meta.source}")
    print(f"  -> {meta.install_path}")
    return 0


def cmd_uninstall(pm: PackageManager, args):
    ok = pm.uninstall(args.name, kind=args.kind)
    if ok:
        print(f"Uninstalled '{args.name}' ({args.kind})")
        return 0
    print(f"Package '{args.name}' ({args.kind}) not found.", file=sys.stderr)
    return 1


def cmd_update(pm: PackageManager, args):
    meta = pm.update(args.name, kind=args.kind)
    print(f"Updated '{meta.name}' ({meta.kind}) @ {meta.version}")
    print(f"  source: {meta.source}")
    return 0


def cmd_info(pm: PackageManager, args):
    meta = pm.info(args.name, kind=args.kind)
    if meta is None:
        print(f"Package '{args.name}' ({args.kind}) not found.", file=sys.stderr)
        return 1
    print(f"Name:        {meta.name}")
    print(f"Kind:        {meta.kind}")
    print(f"Version:     {meta.version}")
    print(f"Description: {meta.description}")
    print(f"Author:      {meta.author}")
    print(f"Source:      {meta.source}")
    print(f"Installed:   {meta.installed_at}")
    print(f"Path:        {meta.install_path}")
    if meta.dependencies:
        print(f"Dependencies: {', '.join(meta.dependencies)}")
    return 0


def cmd_init(pm: PackageManager, args):
    directory = args.directory or "."
    path = scaffold_package_json(directory, name=args.name)
    print(f"Created package manifest: {path}")
    return 0


def cmd_validate(pm: PackageManager, args):
    issues = pm.validate(args.source, kind=args.kind)
    if not issues:
        print(f"OK: '{args.source}' is a valid package.")
        return 0
    print(f"Validation failed for '{args.source}':")
    for issue in issues:
        print(f"  - {issue}")
    return 1


# Keep legacy kind aliases in choices so old scripts continue to work.
_KIND_CHOICES = ["agent", "tool", "skill", "bundle", "definition"]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="nova-pkg",
        description="Nova package manager — install agents, tools, and bundles.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output JSON for machine consumption"
    )
    parser.add_argument(
        "--local",
        "-l",
        action="store_true",
        help=f"Operate on the project-local store (<cwd>/{CONFIG_DIR_NAME}/agent)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List installed packages")
    p_list.add_argument(
        "--kind", choices=_KIND_CHOICES, help="Filter by kind (flat mode only)"
    )
    p_list.add_argument(
        "--flat",
        action="store_true",
        help="Show flat list instead of bundle-grouped view",
    )

    # install
    p_install = sub.add_parser("install", help="Install a package from a source")
    p_install.add_argument(
        "source", help="Local path, git:host/repo@ref, or https:// URL"
    )
    p_install.add_argument(
        "--kind",
        choices=_KIND_CHOICES,
        help="Package kind (auto-detected if omitted)",
    )
    p_install.add_argument(
        "--name", help="Target name (defaults to manifest name or source basename)"
    )
    p_install.add_argument(
        "--no-deps",
        action="store_true",
        help="Skip installing Python dependencies",
    )
    p_install.add_argument(
        "--with-binaries",
        action="store_true",
        help="Attempt to install optional binaries (rg, fd)",
    )

    # uninstall
    p_uninstall = sub.add_parser("uninstall", help="Remove an installed package")
    p_uninstall.add_argument("name", help="Package name")
    p_uninstall.add_argument(
        "--kind",
        required=True,
        choices=_KIND_CHOICES,
        help="Package kind",
    )

    # update
    p_update = sub.add_parser(
        "update", help="Update an installed package from its source"
    )
    p_update.add_argument("name", help="Package name")
    p_update.add_argument(
        "--kind",
        required=True,
        choices=_KIND_CHOICES,
        help="Package kind",
    )

    # info
    p_info = sub.add_parser("info", help="Show package metadata")
    p_info.add_argument("name", help="Package name")
    p_info.add_argument(
        "--kind",
        required=True,
        choices=_KIND_CHOICES,
        help="Package kind",
    )

    # init
    p_init = sub.add_parser(
        "init", help="Scaffold a package.json from standard directories"
    )
    p_init.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Target directory (default: current directory)",
    )
    p_init.add_argument("--name", help="Package name (default: directory basename)")

    # validate
    p_validate = sub.add_parser("validate", help="Validate a package source")
    p_validate.add_argument(
        "source", help="Local path, git:host/repo@ref, or https:// URL"
    )
    p_validate.add_argument(
        "--kind",
        choices=_KIND_CHOICES,
        help="Package kind (auto-detected if omitted)",
    )

    args = parser.parse_args(argv)
    pm = PackageManager(local=args.local)

    dispatch = {
        "list": cmd_list,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "update": cmd_update,
        "info": cmd_info,
        "init": cmd_init,
        "validate": cmd_validate,
    }

    try:
        if args.json:
            import json as _json

            if args.command == "list":
                if args.flat:
                    pkgs = pm.list(kind=args.kind)
                    print(
                        _json.dumps(
                            [pkg.model_dump() for pkg in pkgs], ensure_ascii=False
                        )
                    )
                else:
                    views = pm.list_by_bundle()
                    print(
                        _json.dumps(
                            {k: v.model_dump() for k, v in views.items()},
                            ensure_ascii=False,
                        )
                    )
                return 0
            elif args.command == "info":
                meta = pm.info(args.name, kind=args.kind)
                print(
                    _json.dumps(meta.model_dump() if meta else None, ensure_ascii=False)
                )
                return 0
            elif args.command == "install":
                meta = pm.install(
                    args.source,
                    kind=args.kind,
                    name=args.name,
                    no_deps=args.no_deps,
                    with_binaries=args.with_binaries,
                )
                print(_json.dumps(meta.model_dump(), ensure_ascii=False))
                return 0
            elif args.command == "init":
                path = scaffold_package_json(args.directory or ".", name=args.name)
                print(_json.dumps({"created": path}, ensure_ascii=False))
                return 0
            elif args.command == "uninstall":
                ok = pm.uninstall(args.name, kind=args.kind)
                print(_json.dumps({"ok": ok}, ensure_ascii=False))
                return 0
            elif args.command == "update":
                meta = pm.update(args.name, kind=args.kind)
                print(_json.dumps(meta.model_dump(), ensure_ascii=False))
                return 0
            elif args.command == "validate":
                issues = pm.validate(args.source, kind=args.kind)
                print(
                    _json.dumps(
                        {"ok": not issues, "issues": issues}, ensure_ascii=False
                    )
                )
                return 0 if not issues else 1
        result = dispatch[args.command](pm, args)
        return result
    except Exception as exc:
        if args.json:
            import json as _json

            print(_json.dumps({"error": str(exc)}, ensure_ascii=False))
            return 1
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
