#!/usr/bin/env python3
"""Check skills against the real `faucet` CLI: frontmatter, links, every quoted command and flag, and every pipeline config."""

import argparse
import functools
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
FENCE = re.compile(r"^```(\w*)\s*$")
LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
FLAG = re.compile(r"^--[a-z0-9][a-z0-9-]*")


def frontmatter(text):
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    meta, key = {}, None
    for line in text[4:end].splitlines():
        m = re.match(r"^([a-z_-]+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            meta[key] = "" if val in (">-", ">", "|", "|-") else val
        elif key and line.startswith("  "):
            meta[key] = (meta[key] + " " + line.strip()).strip()
    return meta


def code_blocks(text):
    """(lang, body) for every fenced block."""
    out, lang, buf = [], None, []
    for line in text.splitlines():
        m = FENCE.match(line)
        if m and lang is None:
            lang, buf = m.group(1) or "text", []
        elif line.strip() == "```" and lang is not None:
            out.append((lang, "\n".join(buf)))
            lang = None
        elif lang is not None:
            buf.append(line)
    return out


def command_lines(body):
    """Logical `faucet ...` lines, with backslash continuations joined."""
    joined, cur = [], ""
    for raw in body.splitlines():
        line = raw.rstrip()
        cur = f"{cur} {line.rstrip(chr(92)).strip()}" if cur else line.rstrip(chr(92)).strip()
        if not line.endswith("\\"):
            joined.append(cur.strip())
            cur = ""
    if cur:
        joined.append(cur.strip())
    out = []
    for line in joined:
        line = re.sub(r"^\$\s+", "", line)
        line = line.split(" #", 1)[0].strip()
        for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", line):
            part = re.sub(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+", "", part.strip())
            if part.startswith("faucet "):
                out.append(part)
    return out


class Cli:
    def __init__(self, path):
        self.path = path

    @functools.lru_cache(maxsize=None)
    def help(self, words):
        r = subprocess.run([self.path, *words, "--help"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    @functools.lru_cache(maxsize=None)
    def subcommands(self, words):
        text = self.help(words) or ""
        if "Commands:" not in text:
            return set()
        block = text.split("Commands:", 1)[1].split("\n\n", 1)[0]
        return {m.group(1) for m in re.finditer(r"^\s{2}([a-z][a-z0-9-]*)\s", block, re.M)}

    def check(self, line):
        try:
            argv = shlex.split(line)[1:]
        except ValueError as e:
            return [f"unparseable command `{line}`: {e}"]
        words = []
        for tok in argv:
            if tok.startswith("-") or tok.startswith("<") or tok.startswith("$"):
                break
            if tok in self.subcommands(tuple(words)):
                words.append(tok)
            else:
                break
        if not words and argv and not argv[0].startswith("-"):
            return [f"unknown subcommand `faucet {argv[0]}` in `{line}`"]
        text = self.help(tuple(words))
        if text is None:
            return [f"`faucet {' '.join(words)} --help` failed for `{line}`"]
        top = self.help(()) or ""
        errs = []
        for tok in argv:
            m = FLAG.match(tok)
            if m and m.group(0) not in text and m.group(0) not in top:
                errs.append(f"unknown flag {m.group(0)} for `faucet {' '.join(words)}` in `{line}`")
        return errs


def validate(cli, path):
    r = subprocess.run([cli.path, "validate", "--no-secrets", str(path)], capture_output=True, text=True)
    return None if r.returncode == 0 else (r.stderr or r.stdout).strip().splitlines()[-1:]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--faucet", default="faucet")
    args = ap.parse_args(argv)
    cli = Cli(args.faucet)
    if cli.help(()) is None:
        print(f"cannot run `{args.faucet} --help`", file=sys.stderr)
        return 2
    errors, checked_cmds, checked_cfgs = [], 0, 0
    skill_dirs = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    if not skill_dirs:
        errors.append("no skills found under skills/")
    for skill in skill_dirs:
        md = skill / "SKILL.md"
        if not md.exists():
            errors.append(f"{skill.name}: missing SKILL.md")
            continue
        meta = frontmatter(md.read_text())
        if not meta:
            errors.append(f"{skill.name}: SKILL.md has no frontmatter")
        else:
            if meta.get("name") != skill.name:
                errors.append(f"{skill.name}: frontmatter name is {meta.get('name')!r}")
            if not meta.get("description", "").startswith("Use when"):
                errors.append(f"{skill.name}: description must start with 'Use when'")
    for doc in sorted(SKILLS.rglob("*.md")):
        rel = doc.relative_to(ROOT)
        text = doc.read_text()
        for target in LINK.findall(text):
            if re.match(r"^[a-z]+:", target):
                continue
            if not (doc.parent / target).resolve().exists():
                errors.append(f"{rel}: broken link {target}")
        for lang, body in code_blocks(text):
            if lang in ("bash", "sh", "shell", "console", "text"):
                for line in command_lines(body):
                    checked_cmds += 1
                    errors += [f"{rel}: {e}" for e in cli.check(line)]
            if lang in ("yaml", "yml") and re.match(r"^version:\s", body.lstrip()):
                with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
                    f.write(body)
                checked_cfgs += 1
                err = validate(cli, f.name)
                if err:
                    errors.append(f"{rel}: yaml config block fails validate: {err}")
    for cfg in sorted(SKILLS.glob("*/examples/*.yaml")):
        if "pipeline:" not in cfg.read_text():
            continue
        checked_cfgs += 1
        err = validate(cli, cfg)
        if err:
            errors.append(f"{cfg.relative_to(ROOT)}: fails validate: {err}")
    for e in errors:
        print(f"FAIL {e}")
    print(f"{len(skill_dirs)} skills, {checked_cmds} commands, {checked_cfgs} configs checked; {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
