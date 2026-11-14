"""Copy rendered view PNGs out of the temp game folder for inspection (no PIL in Python 2.7)."""
import shutil


def compose(paths, prefix):
    for i, p in enumerate(paths):
        shutil.copy(p, '%s_%d.png' % (prefix, i))
