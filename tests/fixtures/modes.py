"""An agent that turns on the terminal modes interactive claude turns on.

Measured: claude enables mouse 1000/1002/1003/1006, focus reporting 1004 and
bracketed paste 2004 at startup. The overlay switches mouse tracking off while
it is up, so waking has to hand the agent's modes back rather than leave the
terminal in the runner's configuration.
"""

import sys
import time

sys.stdout.write("\x1b[?1049h\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h\x1b[?1004h\x1b[?2004h")
sys.stdout.flush()

while True:
    sys.stdout.write("\x1b[2;1Hworking")
    sys.stdout.flush()
    time.sleep(0.1)
