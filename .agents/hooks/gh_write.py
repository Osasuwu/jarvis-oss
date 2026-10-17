"""Which shell commands are `gh` writes: the one detector two PreToolUse hooks share (#278).

`github-authority-guard.py` and `literal-gate.py` both import it. They decide "is this a gh
write" here, and each keeps its own decision about what to do with one: the guard checks a
write against the human's authorities, the literal gate scans the text it sends.

A gh call is found by parsing, not by matching text:

- The command is tokenized as a shell would, quotes honoured, so a quoted argument such as
  `--body "... gh pr merge ..."` stays one word and is not a call. Input that cannot be
  tokenized falls back to a whitespace split, so a parse error cannot hide a call. When the
  command holds a backslash it is also tokenized with the backslash kept, because PowerShell
  and Windows paths (`C:\\tools\\gh.exe`) do not use it as an escape.
- A heredoc's body is parsed only when the heredoc is fed to an interpreter, by the rule
  `secret-scanner.py` (same directory) applies, loaded from that file rather than copied
  (#285). When a word on the heredoc's opening line names a shell, `python`, `node`, `ssh`
  or another program that runs its standard input (`bash <<EOF`, `ssh host <<EOF`, and
  `bash -c "$(cat <<EOF`, since `bash -c` runs what `cat` prints), the body runs, so its gh
  calls count. The body of a heredoc that writes text (`cat > file <<EOF`,
  `git commit -F - <<EOF`, a PR body on `--body-file -`) is dropped before parsing: it is
  not a command. The choice is made once, on the whole command, where each opening line is
  in view; only the text an `-EncodedCommand` decodes to is checked again. A heredoc whose
  opening line merely mentions an interpreter (`--title "bash fix"`) has its body parsed
  too, which over-blocks a gh write named in that text: the safe direction.
- Every word is a possible program: one whose basename is `gh` or `gh.exe` in any case, bare,
  quoted or path-qualified, with backticks removed (`` `gh pr merge` `` is a substitution),
  starts a call. `ghost`, `sigh` and `xgh.exe` do not.
- Its subcommand is found the way gh's own parser (cobra) finds it: a `-X` or `--name` flag
  without `=` takes the next word as its value. So `gh -R o/r pr merge`, `gh pr -R o/r merge`,
  `gh -Ro/r pr merge` and `gh --hostname h pr merge` all run `pr merge`.
- The text a wrapper runs is parsed the same way, recursively: `sh -c`/`bash -c` (and zsh,
  dash, ksh, fish), `pwsh`/`powershell -Command`, `-CommandWithArgs` and `-EncodedCommand`,
  `cmd /c` and `/k`, `eval`, `Invoke-Expression`/`iex`, and the remote command of
  `ssh [options] host command`. A call found inside an `-EncodedCommand` is marked `encoded`:
  its text is not in the command as the user sees it.

Not parsed: an alias or function, a script file, a shell reading a pipe, another
interpreter's own syntax (`python -c`, or the body of `python <<EOF`: it is parsed as shell,
where `os.system("gh pr merge 1")` holds one quoted word, not a call), a command passed
as one quoted word to a program not listed above (`watch "gh ..."`, `su -c "gh ..."`).

A gh write is one of:

- `gh pr|issue create|edit|comment|review|merge|close|reopen`,
- `gh release|gist create|edit`,
- `gh label delete|edit`,
- `gh api` with a field or an input file (`-f`, `-F`, `--field`, `--raw-field`, `--input`),
  or a method other than GET (`-X`/`--method`, the last one wins). A `graphql` call is one
  only with a field or an input file: that is the only way its query arrives.
"""

from __future__ import annotations

import base64
import importlib.util
import re
import shlex
from pathlib import Path
from typing import NamedTuple


def _load_secret_scanner():
    """`secret-scanner.py` (same directory) holds the heredoc rule this detector shares. Its
    file name is not a module name, so it is loaded by path. A missing or broken file raises,
    and both hooks that import this module then block."""
    path = Path(__file__).resolve().parent / "secret-scanner.py"
    spec = importlib.util.spec_from_file_location("secret_scanner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Drops the bodies of heredocs that write text, keeps those fed to an interpreter (#93, #285).
strip_heredocs = _load_secret_scanner().strip_heredocs

_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "$", "\n"}
_POSIX_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
_POSIX_COMMAND_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*|--command")

_TEXT_VERBS = {
    "pr": {"create", "edit", "comment", "review", "merge", "close", "reopen"},
    "issue": {"create", "edit", "comment", "review", "merge", "close", "reopen"},
    "release": {"create", "edit"},
    "gist": {"create", "edit"},
    "label": {"delete", "edit"},
}
_API_BODY_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")


class GhCall(NamedTuple):
    args: list[str]  # the words after the gh binary, up to the next separator
    encoded: bool  # found inside a pwsh -EncodedCommand


def _tokens(command: str, escape: bool) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    if not escape:
        lexer.escape = ""
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def program_name(token: str) -> str:
    """A word's program name: lowercased basename without `.exe` or backticks."""
    base = token.replace("`", "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base.removesuffix(".exe")


def is_gh_binary(token: str) -> bool:
    return program_name(token) == "gh"


def invocations(command: str) -> list[tuple[str, list[str]]]:
    """Every word of the command as (program name, the arguments after it up to the next
    separator). Heredocs are read as they stand: command_texts strips them first."""
    token_lists = [_tokens(command, escape=True)]
    if "\\" in command:
        token_lists.append(_tokens(command, escape=False))
    calls = []
    for tokens in token_lists:
        for i, tok in enumerate(tokens):
            args = []
            for t in tokens[i + 1 :]:
                if t in _SEPARATORS or set(t) <= set(";&|()"):
                    break
                args.append(t)
            calls.append((program_name(tok), args))
    return calls


def _decode_ps(value: str) -> str:
    """pwsh -EncodedCommand takes base64 of UTF-16LE text."""
    try:
        return base64.b64decode(value, validate=True).decode("utf-16-le")
    except ValueError:  # binascii.Error and UnicodeDecodeError are ValueErrors
        return ""


def nested_commands(prog: str, args: list[str]) -> list[tuple[str, bool]]:
    """(command text, encoded) for each command a wrapper runs. A shell reading a script file
    or a pipe is not seen; the body of a heredoc fed to one stays in the text it is part of
    (strip_heredocs)."""
    if prog in _POSIX_SHELLS:
        # The first word after -c is the script, the rest are its $0, $1...; each is
        # checked, which over-blocks only a gh write passed as data.
        for j, a in enumerate(args):
            if _POSIX_COMMAND_FLAG.fullmatch(a):
                return [(a2, False) for a2 in args[j + 1 :]]
    elif prog in ("pwsh", "powershell"):
        for j, a in enumerate(args):
            # Parameter names are case-insensitive, any unambiguous prefix, `-` or `/`.
            flag = "-" + a[1:].lower() if a[:1] in ("-", "/") else ""
            if len(flag) < 2:
                continue
            if flag in ("-cwa", "-commandwithargs") or "-command".startswith(flag):
                return [(" ".join(args[j + 1 :]), False)]
            if flag == "-ec" or "-encodedcommand".startswith(flag):
                return [(_decode_ps(v), True) for v in args[j + 1 : j + 2]]
    elif prog == "cmd":
        for j, a in enumerate(args):
            if a.lower() in ("/c", "/k"):
                return [(" ".join(args[j + 1 :]), False)]
    elif prog in ("eval", "iex", "invoke-expression"):
        return [(" ".join(args), False)]
    elif prog == "ssh":
        remote = _ssh_remote_command(args)
        return [(remote, False)] if remote else []
    return []


# ssh options that take a value (OpenSSH getopt string), glued (`-p22`) or as the next word.
_SSH_VALUE_FLAGS = set("BbcDEeFIiJLlmOoPpQRSWw")


def _ssh_remote_command(args: list[str]) -> str:
    """The command `ssh [options] destination command...` runs on the remote shell."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or a == "-":
            break
        for k, ch in enumerate(a[1:], start=1):
            if ch in _SSH_VALUE_FLAGS:
                if k == len(a) - 1:
                    i += 1  # the value is the next word
                break
        i += 1
    return " ".join(args[i + 1 :])


def command_texts(command: str, encoded: bool = False) -> list[tuple[str, bool]]:
    """The command and every command text nested in it, at any depth, as (text, encoded),
    each without the bodies of its text heredocs (strip_heredocs)."""
    return _nested_texts(strip_heredocs(command), encoded)


def _nested_texts(command: str, encoded: bool) -> list[tuple[str, bool]]:
    texts = [(command, encoded)]
    for prog, args in invocations(command):
        for nested, enc in nested_commands(prog, args):
            # A nested text is part of this one, already stripped with each heredoc's opening
            # line in view; stripping it again would lose `bash -c "$(cat <<EOF`'s `bash`.
            # Only decoded text is new.
            texts.extend(command_texts(nested, True) if enc else _nested_texts(nested, encoded))
    return texts


def flag_values(args: list[str], *names: str) -> list[str]:
    """Values of a flag given as `--name value`, `--name=value` or `-Xvalue`."""
    values = []
    for i, a in enumerate(args):
        for n in names:
            if a == n and i + 1 < len(args):
                values.append(args[i + 1])
            elif n.startswith("--") and a.startswith(n + "="):
                values.append(a[len(n) + 1 :])
            elif not n.startswith("--") and a.startswith(n) and len(a) > len(n):
                values.append(a[len(n) :])
    return values


def api_method(args: list[str]) -> str:
    methods = flag_values(args, "-X", "--method")
    if methods:
        return methods[-1].upper()
    # gh api switches to POST when any field or input is given.
    if flag_values(args, *_API_BODY_FLAGS):
        return "POST"
    return "GET"


def command_path(args: list[str]) -> list[tuple[int, str]]:
    """The first two positional words, with their index, found the way gh's command parser
    (cobra) finds a subcommand: a `-X` or `--name` flag without `=` consumes the next word as
    its value. (The boolean flags cobra knows there, `--help` and `--version`, print help
    instead of running anything, so reading them as value flags cannot hide a write.)
    """
    path = []
    i = 0
    while i < len(args) and len(path) < 2:
        a = args[i]
        if not a.startswith("-"):
            path.append((i, a))
        elif "=" not in a and (a.startswith("--") or len(a) == 2):
            i += 1
        i += 1
    return path


def is_write(args: list[str]) -> bool:
    """Whether the gh call with these arguments is a write (module docstring)."""
    words = [w for _, w in command_path(args)]
    if words[:1] == ["api"]:
        return api_method(args) != "GET" or bool(flag_values(args, *_API_BODY_FLAGS))
    return len(words) == 2 and words[1] in _TEXT_VERBS.get(words[0], ())


def gh_calls(command: str) -> list[GhCall]:
    """Every gh call in the command and in the commands nested in it."""
    return [
        GhCall(args, encoded)
        for text, encoded in command_texts(command)
        for prog, args in invocations(text)
        if prog == "gh"
    ]


def gh_writes(command: str) -> list[GhCall]:
    """Every gh call in the command, nested ones included, that is a write."""
    return [call for call in gh_calls(command) if is_write(call.args)]
