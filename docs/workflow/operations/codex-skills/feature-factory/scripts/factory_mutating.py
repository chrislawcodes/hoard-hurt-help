#!/usr/bin/env python3
"""Decorator helpers for classifying feature-factory command handlers."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Iterator
from functools import wraps
from typing import Any

from factory_telemetry_commands import command_telemetry_scope


def _attach_command_flag(func: Callable[..., Any], attr_name: str, command_name: str) -> Callable[..., Any]:
    setattr(func, attr_name, command_name)
    return func


def mutates_state(command_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a command handler as state mutating."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if args and command_name in {
                "checkpoint",
                "reconcile",
                "deliver",
            }:
                slug = getattr(args[0], "slug", None)
                stage = getattr(args[0], "stage", None)
                with command_telemetry_scope(slug, command_name, stage):
                    return func(*args, **kwargs)
            return func(*args, **kwargs)

        return _attach_command_flag(wrapped, "__ff_mutates_state__", command_name)

    return decorator


def readonly_command(command_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a command handler as read-only."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        return _attach_command_flag(wrapped, "__ff_readonly_command__", command_name)

    return decorator


def _canonical_name(handler: Callable[..., Any]) -> str:
    mutating = getattr(handler, "__ff_mutates_state__", None)
    if isinstance(mutating, str) and mutating:
        return mutating
    readonly = getattr(handler, "__ff_readonly_command__", None)
    if isinstance(readonly, str) and readonly:
        return readonly
    name = getattr(handler, "__name__", "")
    if isinstance(name, str) and name.startswith("command_"):
        return name[len("command_") :].replace("_", "-")
    return str(name)


def collect_mutating_command_names(handlers: Iterable[Callable[..., Any]]) -> frozenset[str]:
    """Return the canonical names for handlers tagged as mutating."""
    names = {
        str(getattr(handler, "__ff_mutates_state__", ""))
        for handler in handlers
        if isinstance(getattr(handler, "__ff_mutates_state__", None), str) and str(getattr(handler, "__ff_mutates_state__", "")).strip()
    }
    return frozenset(names)


def all_classified_names(handlers: Iterable[Callable[..., Any]]) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return mutating, readonly, and undecorated command-name sets."""
    mutating: set[str] = set()
    readonly: set[str] = set()
    undecorated: set[str] = set()
    for handler in handlers:
        name = _canonical_name(handler)
        has_mutating = isinstance(getattr(handler, "__ff_mutates_state__", None), str)
        has_readonly = isinstance(getattr(handler, "__ff_readonly_command__", None), str)
        if has_mutating:
            mutating.add(name)
        elif has_readonly:
            readonly.add(name)
        else:
            undecorated.add(name)
    return frozenset(mutating), frozenset(readonly), frozenset(undecorated)


def enumerate_subparser_handlers(parser: argparse.ArgumentParser) -> Iterator[tuple[str, Callable[..., Any]]]:
    """Yield (subcommand_name, handler) pairs from an argparse parser."""
    for action in getattr(parser, "_actions", []):
        if not isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
            continue
        for name, subparser in action.choices.items():
            handler = getattr(subparser, "_defaults", {}).get("func")
            if callable(handler):
                yield name, handler
        break
