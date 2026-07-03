"""Extract real user messages + assistant text replies from CCD session jsonl files.

Filters out: tool results, tool calls, system reminders, hook injections, autonomous queue ops.
Output: compact JSONL with {sess, proj, ts, role, len, text} for downstream analysis.

Universal path: ~/.claude/projects/*/*.jsonl on any device/OS.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

ROOT = Path.home() / ".claude" / "projects"

SYS_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
HOOK_RE = re.compile(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", re.S)
COMMAND_TAG_RE = re.compile(r"<command-(name|message|args)>.*?</command-\1>", re.S)
LOCAL_CMD_RE = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)


def clean_user_text(s: str) -> str:
    s = SYS_REMINDER_RE.sub("", s)
    s = HOOK_RE.sub("", s)
    s = COMMAND_TAG_RE.sub("", s)
    s = LOCAL_CMD_RE.sub("", s)
    return s.strip()


def extract_text_from_content(content) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p) if parts else None
    return None


def is_tool_result(content) -> bool:
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return False


def process_file(fp: Path, out_f) -> int:
    sess = fp.stem
    project = fp.parent.name
    n = 0
    with fp.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            t = obj.get("type")
            if t not in ("user", "assistant"):
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if t == "user":
                if is_tool_result(content):
                    continue
                text = extract_text_from_content(content)
                if not text:
                    continue
                text = clean_user_text(text)
                if not text or len(text) < 2:
                    continue
                if text.startswith("<command-") or text.startswith("[Request interrupted"):
                    continue
            else:
                text = extract_text_from_content(content)
                if not text:
                    continue
                text = text.strip()
                if not text:
                    continue
            ts = obj.get("timestamp", "")
            rec = {"sess": sess, "proj": project, "ts": ts,
                   "role": "u" if t == "user" else "a", "len": len(text), "text": text}
            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n


def main(out_path: str):
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(ROOT.glob("*/*.jsonl"))
    total = sessions = 0
    with out.open("w", encoding="utf-8") as out_f:
        for fp in files:
            n = process_file(fp, out_f)
            total += n
            if n > 0:
                sessions += 1
    print(f"files: {len(files)}  sessions_with_msgs: {sessions}  msgs: {total}")
    print(f"out: {out}  size: {out.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "comms_extract.jsonl"
    main(out_path)
