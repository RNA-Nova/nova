"""CLI entry for the Nova package manager.

Usage::

    nova-pkg list [--flat] [--configured]
    nova-pkg install <source> [-l] [--no-deps] [--dry-run] [--editable]
    nova-pkg uninstall <name_or_source> [-l]
    nova-pkg update <name_or_source> [-l]
    nova-pkg info <name_or_source> [-l]
    nova-pkg init [directory]
    nova-pkg validate <source>
"""

import argparse
import asyncio
import os
import sys

from nova_harness.core.config.defaults import CONFIG_DIR_NAME
from nova_harness.core.package import PackageManager
from nova_harness.core.package.scaffold import scaffold_pyproject_nova_section
from nova_harness.core.utils.version import harness_version


def _fmt_pkg(pkg):
    return f"  {pkg.name:20} {pkg.version:10}  {pkg.source or pkg.description or ''}"


def _render_package_view(views):
    if not views:
        print("No packages installed.")
        return
    for name, view in views.items():
        ver = f" @{view.version}" if view.version else ""
        print(f"\nPackage: {name}{ver}")
        if view.description:
            print(f"  {view.description}")
        agents = ", ".join(a.name for a in view.agents) or "(none)"
        tools = ", ".join(t.name for t in view.tools) or "(none)"
        skills = ", ".join(s.name for s in view.skills) or "(none)"
        extensions = ", ".join(e.name for e in view.extensions) or "(none)"
        prompts = ", ".join(p.name for p in view.prompts) or "(none)"
        user_tools = ", ".join(t.name for t in view.user_tools) or "(none)"
        personas = ", ".join(p.name for p in view.personas) or "(none)"
        print(f"  Agents:    {agents}")
        print(f"  Tools:     {tools}")
        print(f"  Skills:    {skills}")
        print(f"  Extensions: {extensions}")
        print(f"  Prompts:   {prompts}")
        print(f"  User tools: {user_tools}")
        print(f"  Personas:  {personas}")


def cmd_list(pm: PackageManager, args):
    if args.configured:
        configured = pm.list_configured_packages(local=args.local)
        if not configured:
            print("No configured packages.")
            return 0
        header = f"  {'Scope':8} {'Installed':10} {'Filtered':9}  Source"
        print(header)
        print("  " + "-" * len(header))
        for pkg in configured:
            installed = "yes" if pkg.installed_path else "no"
            filtered = "yes" if pkg.filtered else "no"
            print(f"  {pkg.scope.value:8} {installed:10} {filtered:9}  {pkg.source}")
        return 0

    if args.flat:
        pkgs = pm.list(local=args.local)
        if not pkgs:
            print("No packages installed.")
            return 0
        header = f"  {'Name':20} {'Version':10}  Source / Description"
        print(header)
        print("  " + "-" * len(header))
        for pkg in pkgs:
            print(_fmt_pkg(pkg))
        return 0

    views = pm.list_with_resources(local=args.local)
    if not views:
        print("No packages installed.")
        return 0
    _render_package_view(views)
    return 0


def cmd_install(pm: PackageManager, args):
    source = args.source
    meta = pm.install_and_persist(
        source,
        local=args.local,
        no_deps=args.no_deps,
        dry_run=args.dry_run,
        editable=args.editable,
    )
    if args.dry_run:
        print(f"[dry-run] Would install '{meta.name}' @ {meta.version}")
    else:
        print(f"Installed '{meta.name}' @ {meta.version}")
    print(f"  source: {meta.source}")
    print(f"  -> {meta.install_path}")
    return 0


def cmd_uninstall(pm: PackageManager, args):
    result = pm.uninstall(args.name_or_source, local=args.local)
    if result.removed:
        print(f"Uninstalled '{args.name_or_source}'")
        for message in result.messages:
            print(f"  note: {message}")
        return 0
    if result.messages:
        print(f"Package '{args.name_or_source}' not uninstalled.", file=sys.stderr)
        for message in result.messages:
            print(f"  note: {message}", file=sys.stderr)
        return 1
    print(f"Package '{args.name_or_source}' not found.", file=sys.stderr)
    return 1


async def cmd_update(pm: PackageManager, args):
    if args.name_or_source:
        metas = await pm.update(args.name_or_source, local=args.local)
    else:
        metas = await pm.update_all()
        if not metas:
            print("No updatable packages configured.")
            return 0
    for meta in metas:
        print(f"Updated '{meta.name}' @ {meta.version}")
        print(f"  source: {meta.source}")
    return 0


def cmd_info(pm: PackageManager, args):
    meta = pm.info(args.name_or_source, local=args.local)
    if meta is None:
        print(f"Package '{args.name_or_source}' not found.", file=sys.stderr)
        return 1
    print(f"Name:        {meta.name}")
    print(f"Version:     {meta.version}")
    print(f"Description: {meta.description}")
    print(f"Author:      {meta.author}")
    print(f"Source:      {meta.source}")
    if meta.installed_at:
        print(f"Installed:   {meta.installed_at}")
    print(f"Path:        {meta.install_path}")
    print(f"Editable:    {'yes' if meta.editable else 'no'}")
    if meta.package_name:
        print(f"Python pkg:  {meta.package_name}")
    if meta.dependencies:
        print(f"Dependencies: {', '.join(meta.dependencies)}")
    if meta.requires:
        print(f"Requires:    {', '.join(meta.requires)}")
    return 0


def cmd_init(args):
    directory = args.directory or "."
    path = scaffold_pyproject_nova_section(directory, name=args.name)
    print(f"Created package manifest: {path}")
    return 0


def cmd_validate(pm: PackageManager, args):
    issues = pm.validate(args.source, local=args.local)
    if not issues:
        print(f"OK: '{args.source}' is a valid package.")
        return 0
    print(f"Validation failed for '{args.source}':")
    for issue in issues:
        print(f"  - {issue}")
    return 1


def main(argv=None):
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--json", action="store_true", help="Output JSON for machine consumption"
    )
    common_parser.add_argument(
        "--local",
        "-l",
        action="store_true",
        help=f"Operate on the project-local store (<cwd>/{CONFIG_DIR_NAME})",
    )

    parser = argparse.ArgumentParser(
        prog="nova-pkg",
        description="Nova package manager — install agents, tools, skills, extensions, and packages.",
    )
    # 版本单一事实源是 pyproject.toml（importlib.metadata 读打包/安装元数据）
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {harness_version()}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser(
        "list", parents=[common_parser], help="List installed or configured packages"
    )
    p_list.add_argument(
        "--flat",
        action="store_true",
        help="Show flat list instead of package-grouped view",
    )
    p_list.add_argument(
        "--configured",
        action="store_true",
        help="Show configured package sources (including not installed ones)",
    )

    # install
    p_install = sub.add_parser(
        "install", parents=[common_parser], help="Install a package from a source"
    )
    p_install.add_argument(
        "source",
        nargs="?",
        default=".",
        help="Path, git:host/repo@ref, or https:// URL (default: current directory)",
    )
    p_install.add_argument(
        "--no-deps",
        action="store_true",
        help="Skip installing Python dependencies (the Nova package itself is still installed)",
    )
    p_install.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be installed without making changes",
    )
    p_install.add_argument(
        "--editable",
        "-e",
        action="store_true",
        help="Reference the path source in place instead of copying it",
    )

    # uninstall
    p_uninstall = sub.add_parser(
        "uninstall", parents=[common_parser], help="Remove an installed package"
    )
    p_uninstall.add_argument(
        "name_or_source",
        help="Package name or source spec (path:/git:/https://)",
    )

    # update
    p_update = sub.add_parser(
        "update",
        parents=[common_parser],
        help="Update an installed package from its source",
    )
    p_update.add_argument(
        "name_or_source",
        nargs="?",
        default=None,
        help="Package name or source spec (path:/git:/https://). Omit to update all configured git packages",
    )

    # info
    p_info = sub.add_parser(
        "info", parents=[common_parser], help="Show package metadata"
    )
    p_info.add_argument(
        "name_or_source",
        help="Package name or source spec (path:/git:/https://)",
    )

    # init
    p_init = sub.add_parser(
        "init",
        parents=[common_parser],
        help="Scaffold [tool.nova] in pyproject.toml from standard directories",
    )
    p_init.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Target directory (default: current directory)",
    )
    p_init.add_argument("--name", help="Package name (default: directory basename)")

    # validate
    p_validate = sub.add_parser(
        "validate", parents=[common_parser], help="Validate a package source"
    )
    p_validate.add_argument(
        "source", help="Local path, git:host/repo@ref, or https:// URL"
    )

    args = parser.parse_args(argv)

    def _print_progress(event) -> None:
        # 阶段式进度消息（uv/pip 的原生输出继续直通终端）；
        # --json 模式下保持静默，避免污染机器可读输出。
        if args.json or not event.message:
            return
        if event.percent is not None:
            print(f"[{int(event.percent * 100):3d}%] {event.message}", flush=True)
        else:
            print(f"      {event.message}", flush=True)

    pm = PackageManager(on_progress=_print_progress)

    dispatch = {
        "list": cmd_list,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "update": cmd_update,
        "info": cmd_info,
        "init": lambda pm, args: cmd_init(args),
        "validate": cmd_validate,
    }

    async def _run():
        try:
            if args.json:
                import json as _json

                if args.command == "list":
                    if args.flat:
                        pkgs = pm.list(local=args.local)
                        print(
                            _json.dumps(
                                [pkg.model_dump() for pkg in pkgs], ensure_ascii=False
                            )
                        )
                    else:
                        views = pm.list_with_resources(local=args.local)
                        print(
                            _json.dumps(
                                {k: v.model_dump() for k, v in views.items()},
                                ensure_ascii=False,
                            )
                        )
                    return 0
                elif args.command == "info":
                    meta = pm.info(args.name_or_source, local=args.local)
                    print(
                        _json.dumps(
                            meta.model_dump() if meta else None, ensure_ascii=False
                        )
                    )
                    return 0
                elif args.command == "install":
                    meta = pm.install_and_persist(
                        args.source,
                        local=args.local,
                        no_deps=args.no_deps,
                        dry_run=args.dry_run,
                        quiet=True,
                        editable=args.editable,
                    )
                    payload = meta.model_dump()
                    payload["dry_run"] = args.dry_run
                    print(_json.dumps(payload, ensure_ascii=False))
                    return 0
                elif args.command == "init":
                    path = scaffold_pyproject_nova_section(
                        args.directory or ".", name=args.name
                    )
                    print(_json.dumps({"created": path}, ensure_ascii=False))
                    return 0
                elif args.command == "uninstall":
                    result = pm.uninstall(args.name_or_source, local=args.local)
                    payload = {
                        "ok": result.removed,
                        "messages": result.messages,
                    }
                    print(_json.dumps(payload, ensure_ascii=False))
                    return 0
                elif args.command == "update":
                    if args.name_or_source:
                        metas = await pm.update(args.name_or_source, local=args.local)
                    else:
                        metas = await pm.update_all()
                    print(
                        _json.dumps([m.model_dump() for m in metas], ensure_ascii=False)
                    )
                    return 0
                elif args.command == "validate":
                    issues = pm.validate(args.source, local=args.local)
                    print(
                        _json.dumps(
                            {"ok": not issues, "issues": issues}, ensure_ascii=False
                        )
                    )
                    return 0 if not issues else 1
            result = dispatch[args.command](pm, args)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as exc:
            if args.json:
                import json as _json

                print(_json.dumps({"error": str(exc)}, ensure_ascii=False))
                return 1
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
