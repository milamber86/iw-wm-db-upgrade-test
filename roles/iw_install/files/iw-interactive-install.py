#!/usr/bin/env python3
"""Run IceWarp install.sh in INTERACTIVE mode (no switches) through a pty.

Usage: iw-interactive-install.py <installer_dir> <log_path>

Used when `iw_install_interactive` is true. Automode (`--auto`) is avoided because
install.sh aborts with exit 3 when its 300 s "Waiting for groupware DB" wait
expires (`if [[ "$OPT_AUTO" == "1" ]]; then exit 3; fi`); interactive mode warns,
asks to continue, and then proceeds to wait for the database work.

Prompt forms handled:
  * ask_with_confirmation / getparam -> line ends with "[<opts>]:" (ANSI codes stripped)
  * read -s confirmations            -> line contains "Press ENTER" / "Hit ENTER"
  * "(type 'yes' or 'no')"           -> literal "yes"
Answer rule: "y" when the bracketed default starts with N; Enter otherwise.
Answers are delayed ~0.9 s because ask_with_confirmation drains stdin (clear_stdin)
right before reading.
"""
import os, pty, re, select, signal, subprocess, sys, time

if len(sys.argv) != 3:
    sys.exit("usage: iw-interactive-install.py <installer_dir> <log_path>")
INSTALLER_DIR, LOG_PATH = sys.argv[1], sys.argv[2]
MAX_SECONDS = 4 * 3600
PROMPT_RE = re.compile(r"\[([^\]\n]{0,80})\]\s*:\s*$")
ENTER_RE = re.compile(r"(?i)press\s+enter|hit\s+enter")
YESNO_RE = re.compile(r"(?i)type\s+'?yes'?\s+or\s+'?no'?")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
log = open(LOG_PATH, "ab", buffering=0)

def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

def w(line):
    log.write(f"[{ts()}] DRIVER {line}\n".encode())

w(f"=== interactive install.sh start ({INSTALLER_DIR}) ===")
master, slave = pty.openpty()
env = dict(os.environ, TERM="xterm")
proc = subprocess.Popen(["./install.sh"], cwd=INSTALLER_DIR, stdin=slave, stdout=slave,
                        stderr=slave, env=env, preexec_fn=os.setsid, close_fds=True)
os.close(slave)

buf = b""
pending = None
last_prompt = ""
start = time.time()
last_output = time.time()

while True:
    if time.time() - start > MAX_SECONDS:
        w("=== MAX_SECONDS reached, killing installer ===")
        os.killpg(proc.pid, signal.SIGKILL)
        break
    r, _, _ = select.select([master], [], [], 0.5)
    if r:
        try:
            data = os.read(master, 8192)
        except OSError:
            data = b""
        if not data:
            break
        last_output = time.time()
        log.write(data)
        buf = (buf + data)[-16384:]
        clean = ANSI_RE.sub("", buf.decode(errors="replace")).rstrip()
        lines = clean.splitlines()
        last_line = lines[-1] if lines else ""
        if last_line != last_prompt:
            m = PROMPT_RE.search(last_line)
            if YESNO_RE.search(last_line):
                last_prompt = last_line
                pending = ("yes\n", time.time())
                w(f"prompt yes/no -> 'yes' :: {last_line[-120:]}")
            elif m or ENTER_RE.search(last_line):
                if m:
                    opts = m.group(1).strip()
                    # The default is the upper-case letter: [Y/n] -> Enter, [y/N] -> "y".
                    # (The earlier "starts with n" rule broke on [y/N], where Enter means N,
                    # so dnf aborted and the installer re-asked forever.)
                    if "Y" in opts:
                        answer = "\n"
                    elif "N" in opts:
                        answer = "y\n"
                    else:
                        answer = "\n"
                    why = f"opts=[{opts}] -> default {'Y' if 'Y' in opts else ('N' if 'N' in opts else 'value')}"
                else:
                    answer = "\n"
                    why = "press-enter"
                last_prompt = last_line
                pending = (answer, time.time())
                w(f"prompt {why} -> {answer.strip() or '<Enter>'!r} :: {last_line[-120:]}")
    if pending and time.time() - pending[1] > 0.9:
        answer, _ = pending
        pending = None
        os.write(master, answer.encode())
        # Drop what we have seen so an identical prompt that follows (e.g. the
        # RPM Fusion GPG-key "Is this ok [y/N]:" right after the dnf transaction
        # one) is detected and answered again instead of being deduplicated.
        buf = b""
        last_prompt = ""
    if time.time() - last_output > 600:
        w(f"=== no output for {int(time.time()-last_output)}s ===")
        last_output = time.time()

rc = proc.poll()
if rc is None:
    proc.wait()
    rc = proc.returncode
w(f"=== interactive install.sh exit rc={rc} ===")
try:
    os.close(master)
except OSError:
    pass
sys.exit(0)
