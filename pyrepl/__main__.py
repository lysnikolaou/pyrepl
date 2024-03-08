import code
import sys

from pyrepl.simple_interact import init, run_multiline_interactive_console


def interactive_console():
    # set sys.{ps1,ps2} just before invoking the interactive interpreter. This
    # mimics what CPython does in pythonrun.c
    if not hasattr(sys, "ps1"):
        sys.ps1 = ">>> "
    if not hasattr(sys, "ps2"):
        sys.ps2 = "... "

    try:
        init()
        run_interactive = run_multiline_interactive_console
    except:
        run_interactive = run_simple_interactive_console
    run_interactive()


def run_simple_interactive_console():
    console = code.InteractiveConsole(filename="<stdin>")
    console.interact(banner='', exitmsg='')


if __name__ == "__main__":
    interactive_console()
