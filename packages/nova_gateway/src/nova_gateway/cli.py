"""nova-gateway 命令行：服务与公共目录生成。"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

import uvicorn
from nova_ai.utils.sanitize import redact_secrets
from nova_logging import LoggingSettings, SinkSettings, configure_logging

from .app import create_app
from .auth import ensure_bind_security, provision_token
from .catalog_builder import main as generate_catalog_main
from .config import load_config

_DEFAULT_TOKEN_FILE = Path.home() / ".nova" / "agent" / "gateway.json"
_DEFAULT_CATALOG_DIR = Path.home() / ".nova" / "agent" / "gateway" / "catalog"


def _target_level(value: str) -> tuple[str, int]:
    target, separator, level_name = value.rpartition("=")
    level = getattr(logging, level_name.upper(), None) if separator else None
    if not target or not isinstance(level, int):
        raise argparse.ArgumentTypeError("日志目标必须使用 PREFIX=LEVEL 格式")
    return target, level


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nova-gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "generate-catalog"):
        child = sub.add_parser(name)
        child.add_argument("--config", required=True, help="Gateway YAML 配置路径")
        if name == "serve":
            child.add_argument("--host")
            child.add_argument("--port", type=int)
            child.add_argument("--token")
            child.add_argument("--token-file")
            child.add_argument("--allow-origin", action="append", default=[])
            child.add_argument(
                "--log-level",
                choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                default="INFO",
            )
            child.add_argument(
                "--log-format", choices=["json", "console"], default="json"
            )
            child.add_argument("--log-file", type=Path)
            child.add_argument(
                "--log-target",
                action="append",
                type=_target_level,
                default=[],
                metavar="PREFIX=LEVEL",
                help="按 logger 名最长前缀覆盖级别，可重复",
            )
        else:
            child.add_argument("--output-dir", type=Path)
            child.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "generate-catalog":
        output_dir = str(args.output_dir or config.catalog_dir or _DEFAULT_CATALOG_DIR)
        return generate_catalog_main(
            [output_dir, "--pretty"] if args.pretty else [output_dir]
        )
    if args.host is not None:
        config.host = args.host
    if args.port is not None:
        config.port = args.port
    if args.allow_origin:
        config.allow_origins = args.allow_origin
    token, _ = provision_token(
        args.token or config.api_token or os.environ.get("NOVA_GATEWAY_TOKEN"),
        args.token_file,
        _DEFAULT_TOKEN_FILE,
    )
    config.api_token = token
    ensure_bind_security(config)
    # HTTP/SDK 的 INFO/DEBUG 会记录完整 URL、headers 或请求选项，不是
    # 正文调试开关；即使用户覆盖，也只能向 WARNING/ERROR 收紧。
    target_levels = dict(args.log_target)
    for name in ("httpx", "httpx2", "httpcore", "openai", "anthropic"):
        target_levels[name] = max(
            logging.WARNING, target_levels.get(name, logging.WARNING)
        )
    level = getattr(logging, args.log_level)
    sinks = [
        SinkSettings(
            level=level,
            json_output=args.log_format == "json",
            target_levels=target_levels,
        )
    ]
    if args.log_file is not None:
        sinks.append(
            SinkSettings(
                level=level,
                json_output=True,
                file_path=args.log_file,
                target_levels=target_levels,
            )
        )
    logging_runtime = configure_logging(
        LoggingSettings(sinks=tuple(sinks), redactor=redact_secrets)
    )
    try:
        app = create_app(config)
        uvicorn.run(
            app,
            host=config.host,
            port=config.port,
            access_log=False,
            # 让 Uvicorn 自身 logger 传播到已装配的 root，而非另装默认出口。
            log_config={"version": 1, "disable_existing_loggers": False},
        )
    finally:
        logging_runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
