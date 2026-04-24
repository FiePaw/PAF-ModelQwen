#!/usr/bin/env python3
"""
main.py – CLI entry point for AIChatScraper (Qwen AI)

Usage
-----
  # Single prompt, new conversation
  python main.py --prompt "Explain async/await" --mode new

  # Continue existing conversation
  python main.py --prompt "Show me an example" --mode continue

  # Multiple prompts from a file (one per line)
  python main.py --prompts-file prompts.txt --mode new --concurrent 3

  # Non-headless (show browser window)
  python main.py --prompt "Hello" --no-headless

  # Save code files extracted from response
  python main.py --prompt "Write a Python parser" --save-code

  # Use a specific cookie file
  python main.py --prompt "Hi" --cookie cookies/account1.json

  # Output to custom file
  python main.py --prompt "Hi" --output my_result.json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# ── Ensure project root is importable ────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config import CODE_OUTPUT_DIR, COOKIES_DIR, OUTPUT_DIR
from scrapers.qwen_scraper import QwenScraper
from scrapers.utils import safe_filename, save_json, setup_logger, timestamped_filename

logger = setup_logger("main")


# ─── CLI argument parsing ─────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aichat-scraper",
        description="Async Qwen AI chat scraper with cookie rotation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Prompt input (mutually exclusive)
    prompt_group = p.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument(
        "--prompt", "-p",
        metavar="TEXT",
        help="Prompt string to send to Qwen AI",
    )
    prompt_group.add_argument(
        "--prompts-file", "-f",
        metavar="FILE",
        type=Path,
        help="Path to a text file with one prompt per line (enables concurrent mode)",
    )

    # Mode
    p.add_argument(
        "--mode", "-m",
        choices=["new", "continue"],
        default="new",
        help="'new' starts a fresh chat; 'continue' appends to the current one (default: new)",
    )

    # Browser
    p.add_argument(
        "--no-headless",
        action="store_true",
        default=False,
        help="Show the browser window (default: headless)",
    )

    # Cookies
    p.add_argument(
        "--cookie",
        metavar="FILE",
        type=Path,
        default=None,
        help="Path to a single cookie .json file (overrides cookies_dir for single-account use)",
    )
    p.add_argument(
        "--cookies-dir",
        metavar="DIR",
        type=Path,
        default=COOKIES_DIR,
        help=f"Directory with multiple account cookie .json files (default: {COOKIES_DIR})",
    )

    # Output
    p.add_argument(
        "--output", "-o",
        metavar="FILE",
        type=Path,
        default=None,
        help="Output JSON filename (default: auto-generated timestamp name in output/)",
    )
    p.add_argument(
        "--save-code",
        action="store_true",
        default=False,
        help="Extract and save code blocks from response to output/code/",
    )

    # Concurrency
    p.add_argument(
        "--concurrent",
        metavar="N",
        type=int,
        default=3,
        help="Max simultaneous browser instances when using --prompts-file (default: 3)",
    )

    return p


# ─── Single-prompt run ────────────────────────────────────────────────────────

async def run_single(args: argparse.Namespace) -> None:
    headless = not args.no_headless
    prompt = args.prompt

    logger.info("=== Single prompt mode ===")
    logger.info("Prompt: %s", prompt[:80] + ("…" if len(prompt) > 80 else ""))

    async with QwenScraper(
        headless=headless,
        cookies_path=args.cookie,
        cookies_dir=args.cookies_dir,
    ) as scraper:
        result = await scraper.scrape(prompt, mode=args.mode)

    _handle_result(result, args)


# ─── Multi-prompt concurrent run ─────────────────────────────────────────────

async def run_multi(args: argparse.Namespace) -> None:
    prompts_file: Path = args.prompts_file
    if not prompts_file.exists():
        logger.error("Prompts file not found: %s", prompts_file)
        sys.exit(1)

    prompts = [
        line.strip()
        for line in prompts_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not prompts:
        logger.error("No prompts found in %s", prompts_file)
        sys.exit(1)

    logger.info("=== Concurrent mode: %d prompt(s), max %d at a time ===", len(prompts), args.concurrent)

    headless = not args.no_headless
    results = await QwenScraper.scrape_many(
        prompts=prompts,
        mode=args.mode,
        headless=headless,
        cookies_dir=args.cookies_dir,
        max_concurrent=args.concurrent,
    )

    # Save all results to a single JSON file
    output_path = _resolve_output_path(args, prefix="batch")
    save_json(results, output_path)
    logger.info("Batch results saved → %s", output_path)

    # Summary
    success = sum(1 for r in results if r.get("success"))
    logger.info("Done: %d/%d successful", success, len(results))


# ─── Result handling ──────────────────────────────────────────────────────────

def _resolve_output_path(args: argparse.Namespace, prefix: str = "response") -> Path:
    if args.output:
        return OUTPUT_DIR / args.output if not args.output.is_absolute() else args.output
    slug = safe_filename(args.prompt or prefix, max_len=30)
    return OUTPUT_DIR / timestamped_filename(slug)


def _handle_result(result: dict, args: argparse.Namespace) -> None:
    if not result["success"]:
        logger.error("Scrape FAILED: %s", result.get("error"))
        sys.exit(1)

    response = result["response"]
    logger.info("─" * 60)
    logger.info("RESPONSE (%d chars):", len(response))
    # Print first 500 chars to console
    preview = response[:500] + ("…" if len(response) > 500 else "")
    print("\n" + preview + "\n")
    logger.info("─" * 60)

    # Save JSON
    output_path = _resolve_output_path(args)
    save_json(result, output_path)
    logger.info("Result JSON saved → %s", output_path)

    # Optionally save code files
    if args.save_code and result["code_blocks"]:
        slug = safe_filename(args.prompt or "response", max_len=20)
        code_dir = CODE_OUTPUT_DIR / slug
        from scrapers.utils import save_code_files as _save_code
        paths = _save_code(result["code_blocks"], code_dir, prefix=slug)
        for p in paths:
            logger.info("Code file saved → %s", p)
    elif args.save_code:
        logger.info("No code blocks found in response")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.prompt:
            asyncio.run(run_single(args))
        else:
            asyncio.run(run_multi(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
