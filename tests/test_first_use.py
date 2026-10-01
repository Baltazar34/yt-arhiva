#!/usr/bin/env python3
"""Prvo koriscenje: brew install (Ollama/whisper-cpp) i ollama pull sa napretkom — bez mreze (lazni brew/ollama)."""
import os, shutil, sys, tempfile, threading
from pathlib import Path
from collections import deque
ROOT = Path(__file__).resolve().parents[1]
os.environ["YTA_DATA"] = tempfile.mkdtemp(prefix="yta-fu-")
sys.path.insert(0, str(ROOT))
import panel  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="yta-fu-bin-"))
state = tmp / "models.txt"
(tmp / "ollama.tpl").write_text("#!/bin/sh\nif [ \"$1\" = pull ]; then\n"
                                "  for p in 10 55 100; do printf 'pulling abc:  %s%%\\r' \"$p\"; done\n"
                                f"  echo \"$2\" >> '{state}'; echo success\nfi\n")
(tmp / "brew").write_text(f"#!/bin/sh\n[ \"$1\" = install ] || exit 2\n"
                          f"cp '{tmp}/ollama.tpl' '{tmp}/ollama'; chmod +x '{tmp}/ollama'; echo installed $2\n")
(tmp / "brew").chmod(0o755)
os.environ["PATH"] = f"{tmp}:/usr/bin:/bin"
panel.BREW_BINS = (str(tmp / "brew"),)
panel.ollama_tags = lambda: state.read_text().split() if state.exists() else []
panel.start_ollama = lambda: None


class FakeJob:
    def __init__(self):
        self.log, self.stages, self.cancel = deque(maxlen=200), [], threading.Event()
    def say(self, m): self.log.append(m)
    def set_stage(self, name, status, pct=None, info=None): self.stages.append((name, status, pct, info))
    run = panel.Job.run
    check = panel.Job.check
    procs = set()


ok = fail = 0
def t(name, fn):
    global ok, fail
    try:
        fn(); ok += 1; print("PASS", name)
    except Exception as e:  # noqa: BLE001
        fail += 1; print("FAIL", name, e)


def t_install_and_pull():
    j = FakeJob(); j.procs = set()
    assert not __import__("shutil").which("ollama")
    panel.ensure_ollama("fake:1b", j)
    assert __import__("shutil").which("ollama"), "ollama nije instaliran"
    assert "fake:1b" in panel.ollama_tags()
    pcts = [s[2] for s in j.stages if s[2] is not None]
    assert 55 in pcts and 100 in pcts, pcts
    assert any("prvo korišćenje" in (s[3] or "") for s in j.stages)


def t_no_space():
    j = FakeJob(); j.procs = set()
    old = panel.free_gb
    panel.free_gb = lambda p: 5.0
    try:
        panel.ensure_ollama("veliki:70b", j)
        raise AssertionError("trebalo je da javi nema mesta")
    except RuntimeError as e:
        assert "nema mesta" in str(e), e
    finally:
        panel.free_gb = old


def t_manual_mode():
    j = FakeJob(); j.procs = set()
    panel.SETTINGS["auto_fetch"] = False
    try:
        panel.ensure_ollama("drugi:2b", j)
        raise AssertionError("rucni rezim ne sme da preuzima")
    except RuntimeError as e:
        assert "nije preuzet" in str(e), e
    finally:
        panel.SETTINGS["auto_fetch"] = True


def t_whisper_dir():
    """Folder whisper modela: YTA_WHISPER_DIR > ~/Library/Application Support/YT Arhiva/whisper."""
    wd = panel.core.whisper_models_dir
    home = Path(tempfile.mkdtemp(prefix="yta-fu-home-"))
    new = home / "Library" / "Application Support" / "YT Arhiva" / "whisper"
    try:
        assert wd({}, home) == new, wd({}, home)
        assert wd({"YTA_WHISPER_DIR": "  "}, home) == new, "prazna vrednost se ignorise"
        assert wd({"YTA_WHISPER_DIR": str(home / "drugi")}, home) == home / "drugi", "YTA_WHISPER_DIR ima prednost"
        assert wd({"YTA_WHISPER_DIR": "~/w"}, home) == Path("~/w").expanduser(), "~ se razvija"
        assert not new.exists(), "izbor foldera nista ne pravi"
        assert panel.WHISPER_DIR == wd(), "panel koristi isti izbor"
    finally:
        shutil.rmtree(home, ignore_errors=True)


def t_whisper_bin():
    """Rezervni python whisper: YTA_WHISPER_BIN (putanja ili komanda), inace `whisper` na PATH-u."""
    fb = Path(tempfile.mkdtemp(prefix="yta-fu-wb-"))
    w = fb / "whisper"
    w.write_text("#!/bin/sh\nexit 0\n")
    w.chmod(0o755)
    old_path, old_env = os.environ["PATH"], os.environ.pop("YTA_WHISPER_BIN", None)
    try:
        assert panel.core.whisper_bin() is None, "nema whisper-a na PATH-u"
        os.environ["PATH"] = f"{fb}:{old_path}"
        assert panel.core.whisper_bin() == str(w), "whisper sa PATH-a"
        os.environ["PATH"] = old_path
        os.environ["YTA_WHISPER_BIN"] = str(w)
        assert panel.core.whisper_bin() == str(w), "putanja iz YTA_WHISPER_BIN"
        os.environ["YTA_WHISPER_BIN"] = str(fb / "nema")
        assert panel.core.whisper_bin() is None, "nepostojeca putanja -> None"
    finally:
        os.environ["PATH"] = old_path
        os.environ.pop("YTA_WHISPER_BIN", None)
        if old_env is not None:
            os.environ["YTA_WHISPER_BIN"] = old_env
        shutil.rmtree(fb, ignore_errors=True)


t("brew install + ollama pull sa napretkom", t_install_and_pull)
t("nema mesta -> jasna greska, bez preuzimanja", t_no_space)
t("rucni rezim -> greska umesto preuzimanja", t_manual_mode)
t("whisper modeli: YTA_WHISPER_DIR > podrazumevani", t_whisper_dir)
t("rezervni python whisper: YTA_WHISPER_BIN ili PATH", t_whisper_bin)
print(f"{ok}/{ok + fail} prošlo")
sys.exit(1 if fail else 0)
