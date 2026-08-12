"""Command line entry point: python -m gamba [run|ask|doctor|init-config]"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import (DEFAULT_CONFIG_PATH, PROVIDER_DEFAULTS,
                     apply_provider_defaults, load_config, load_dotenv_files,
                     save_config)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gamba",
        description="Read the screen and answer questions about it, fast.",
    )
    parser.add_argument("--config", type=Path, default=None,
                        help=f"config file (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--provider", choices=sorted(PROVIDER_DEFAULTS), default=None,
                        help="override the provider, with its default model and pricing")
    parser.add_argument("--model", default=None, help="override the model id")
    parser.add_argument("--api-key-env", default=None, metavar="NAME",
                        help="read the key from your own environment variable "
                             "instead of the provider default")
    parser.add_argument("--base-url", default=None, metavar="URL",
                        help="point at your own OpenAI-compatible endpoint")
    parser.add_argument("--api-name", default=None, metavar="LABEL",
                        help="what to call this endpoint in the UI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="start the overlay app (default)")
    sub.add_parser("doctor", help="check dependencies, key, screen access")
    sub.add_parser("init-config", help="write a config file with the defaults")

    ask = sub.add_parser("ask", help="one-shot: screenshot, answer, print, exit")
    ask.add_argument("question", nargs="*", help="question to ask about the screen")
    ask.add_argument("--deadline", type=float, default=None,
                     help="override the answer budget in seconds")
    ask.add_argument("--save-shot", type=Path, default=None,
                     help="also write the screenshot that was sent to this path")

    args = parser.parse_args(argv)
    dotenv_files = load_dotenv_files()
    config = load_config(args.config)
    if args.provider:
        apply_provider_defaults(config, args.provider)
    if args.model:
        config.model.model = args.model
    if args.api_key_env:
        config.model.api_key_env = args.api_key_env
    if args.base_url:
        config.model.base_url = args.base_url
    if args.api_name:
        config.model.api_name = args.api_name
    command = args.command or "run"

    if command == "init-config":
        path = save_config(config, args.config)
        print(f"wrote {path}")
        return 0
    if command == "doctor":
        return _doctor(config, dotenv_files)
    if command == "ask":
        return _ask(config, args)
    return _run(config)


def _run(config) -> int:
    from .app import GambaApp
    from .provider import MissingAPIKey

    try:
        app = GambaApp(config)
    except (MissingAPIKey, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    app.run()
    return 0


def _ask(config, args) -> int:
    from .capture import capture_once, encode_jpeg
    from .provider import MissingAPIKey, create_provider

    question = " ".join(args.question) or config.watch.question
    try:
        provider = create_provider(config.model, config.resolved_api_key())
    except (MissingAPIKey, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    frame = capture_once(config.capture)
    image = encode_jpeg(frame.image, config.capture.jpeg_quality)
    if args.save_shot:
        args.save_shot.write_bytes(image)

    started = time.monotonic()
    answer = provider.stream_answer(
        question=question,
        image_jpeg=image,
        on_text=lambda chunk: (sys.stdout.write(chunk), sys.stdout.flush()),
        deadline_seconds=args.deadline,
    )
    print()
    if answer.error:
        print(f"error: {answer.error}", file=sys.stderr)
        return 1
    cost = (
        answer.input_tokens * config.model.input_cost_per_mtok / 1e6
        + answer.output_tokens * config.model.output_cost_per_mtok / 1e6
    )
    note = " (truncated at deadline)" if answer.truncated else ""
    print(
        f"[{config.model.model} · {time.monotonic() - started:.2f}s{note} · "
        f"{answer.input_tokens} in / {answer.output_tokens} out · ${cost:.5f}]",
        file=sys.stderr,
    )
    return 0


def _doctor(config, dotenv_files=()) -> int:
    ok = True
    provider = config.model.provider.lower()
    sdk = "anthropic" if provider == "anthropic" else "openai"

    print(f"api:      {config.api_label}")
    print(f"provider: {provider} · model: {config.model.model or '(unset)'}")
    if config.model.base_url:
        print(f"endpoint: {config.model.base_url}")
    for path in dotenv_files:
        print(f"loaded:   {path}")

    for module, why in (
        ("mss", "screen capture"),
        ("PIL", "image encoding"),
        (sdk, f"{provider} API"),
        ("pynput", "global hotkeys"),
        ("tkinter", "overlay window"),
    ):
        try:
            __import__(module)
            print(f"  ok    {module:<10} ({why})")
        except Exception as exc:
            ok = False
            print(f"  FAIL  {module:<10} ({why}): {exc}")

    key, source = config.resolve_api_key()
    if key:
        print(f"  ok    API key found via {source} ({_mask(key)})")
    else:
        ok = False
        print(f"  FAIL  no API key - looked at {source}")
        if config.model.api_key_env:
            from .config import DEFAULT_API_KEY_ENV_VARS

            fallback = DEFAULT_API_KEY_ENV_VARS.get(provider, "OPENAI_API_KEY")
            print(f"        You set a custom name, so only {config.model.api_key_env}")
            print("        is consulted. Clear model.api_key_env (or drop")
            print(f"        --api-key-env) to use {fallback} instead.")
        elif provider == "openai":
            print("        A ChatGPT subscription is billed separately from the API;")
            print("        create a key at https://platform.openai.com/api-keys")
            print("        and make sure the organisation has credit.")
        elif provider == "anthropic":
            print("        A Claude Pro/Max subscription does not include API access;")
            print("        create a key at https://console.anthropic.com/settings/keys")

    if provider == "custom" and not config.model.base_url:
        ok = False
        print("  FAIL  provider \"custom\" needs model.base_url (or --base-url)")

    try:
        from .capture import capture_once

        frame = capture_once(config.capture)
        print(f"  ok    screenshot {frame.image.width}x{frame.image.height}")
    except Exception as exc:
        ok = False
        print(f"  FAIL  screenshot: {exc}")

    print("all good" if ok else "some checks failed")
    return 0 if ok else 1


def _mask(key: str) -> str:
    """Show just enough of the key to tell two of them apart."""
    return f"{key[:6]}…{key[-4:]}" if len(key) > 14 else "set"


if __name__ == "__main__":
    raise SystemExit(main())
