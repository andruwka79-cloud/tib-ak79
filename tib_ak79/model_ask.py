"""Harness-side model call for `runner.run_subject(ask=...)`: a prompt goes to a command on stdin, its stdout comes back as text.

The command is chosen by the operator (for example a local fake in tests, or a model CLI); nothing here assumes how a model CLI
is invoked.  Whether a given model CLI works this way is NOT verified live.

The command does NOT inherit the harness environment: it gets a small allowlist (what it needs to find its executables) plus
whatever the operator names in `extra_env`.  Tokens, API keys and the harness's own variables stay out, so nothing secret can be
echoed back through the model's text to the subject.  It runs in a fresh empty directory, not the repository.

HOME is NOT passed through either: the real home holds ~/.ssh, ~/.config/gh and other logins.  The command gets an empty
temporary home by default, or `home=` - a directory the operator prepared (a dedicated account's profile that holds only that
CLI's login).  Shell wrappers as the command are refused."""
import os
import subprocess
import tempfile

MAX_OUT = 64 * 1024
ENV_ALLOW = ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "LANG", "LC_ALL")
SHELLS = {"sh", "bash", "dash", "zsh", "fish", "ksh", "csh", "tcsh", "env", "xargs", "nohup", "cmd", "powershell", "pwsh", "wsl"}


def model_env(home: str, extra_env: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ENV_ALLOW if k in os.environ}
    env.update({"HOME": home, "USERPROFILE": home, "APPDATA": os.path.join(home, "AppData", "Roaming"),
                "LOCALAPPDATA": os.path.join(home, "AppData", "Local"), "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                "XDG_DATA_HOME": os.path.join(home, ".local", "share"), "TEMP": home, "TMP": home, "TMPDIR": home})
    env.update({str(k): str(v) for k, v in (extra_env or {}).items()})
    return env


def _check_command(argv: list) -> None:
    name = os.path.basename(str(argv[0])).lower() if argv else ""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if not argv or stem in SHELLS:
        raise ValueError("model command must be an executable, not a shell or wrapper: %s" % (name or "empty"))


def cli_ask(argv, timeout: float = 120.0, extra_env: dict | None = None, home: str | None = None):
    """Returns ask(prompt) -> str.  Raises ValueError at construction time for a shell or wrapper as the command."""
    argv = list(argv)
    _check_command(argv)

    def ask(prompt: str) -> str:
        _check_command(argv)
        with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as empty_home:
            cp = subprocess.run(argv, input=prompt.encode("utf-8"), capture_output=True, timeout=timeout, check=False,  # noqa: S603
                                env=model_env(home or empty_home, extra_env), cwd=cwd)
        if cp.returncode != 0:
            raise RuntimeError("model_command_failed:%d" % cp.returncode)
        return cp.stdout[:MAX_OUT].decode("utf-8", errors="replace")
    return ask
