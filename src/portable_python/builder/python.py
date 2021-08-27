import os

import runez

from portable_python import LOG
from portable_python.builder import PythonBuilder


class Cpython(PythonBuilder):
    """Build CPython binaries"""

    base_url = "https://www.python.org/ftp/python"
    _main_python = None

    @classmethod
    def available_modules(cls):
        from portable_python.builder.clibs import LibFFI, Readline, Openssl, Uuid
        from portable_python.builder.compression import Bzip2, Xz, Zlib
        from portable_python.builder.db import Bdb, Gdbm, Sqlite
        from portable_python.builder.tcl import Tcl, Tix, Tk

        return [Bdb, Bzip2, Gdbm, LibFFI, Openssl, Readline, Sqlite, Tcl, Tix, Tk, Uuid, Xz, Zlib]

    @property
    def url(self):
        """Url of source tarball"""
        return f"{self.base_url}/{self.version}/Python-{self.version}.tar.xz"

    def xenv_cflags(self):
        yield "-Wno-unused-command-line-argument"
        yield self.checked_deps_folder("include", prefix="-I")
        yield self.checked_deps_folder("include/readline", prefix="-I")
        yield self.checked_deps_folder("include/openssl", prefix="-I")
        yield self.checked_deps_folder("include/uuid", prefix="-I")

    def xenv_ldflags(self):
        yield self.checked_deps_folder("lib", prefix="-L")

    def c_configure_args(self):
        yield from super().c_configure_args()
        openssl = self.setup.get_module("openssl")
        yield "--with-ensurepip=%s" % ("upgrade" if openssl else "install")
        yield "--enable-optimizations"
        yield "--with-lto"
        if openssl:
            yield f"--with-openssl={self.deps}"

        tcl = self.setup.get_module("tcl")
        if tcl:
            yield f"--with-tcltk-includes=-I{self.deps}/include"
            yield f"--with-tcltk-libs=-L{self.deps}/lib"

    @property
    def main_python(self):
        if self._main_python is None:
            main_python_candidates = ("python", "python%s" % self.version.major, "python%s.%s" % (self.version.major, self.version.minor))
            for f in runez.ls_dir(self.bin_folder):
                if f.name in main_python_candidates:
                    self._main_python = runez.basename(f, extension_marker=None, follow=True)
                    break

        return self._main_python or "python"

    def _finalize(self):
        if self.setup.get_module("openssl"):
            self.run(self.bin_folder / self.main_python, "-mpip", "install", "-U", "pip", "setuptools", "wheel")

        if self.setup.static:
            self._symlink_static_libs()

        self.correct_symlinks()
        self.cleanup_distribution()
        self.run(self.bin_folder / self.main_python, "-mcompileall")
        runez.compress(self.install_folder, self.tarball_path)

    def _symlink_static_libs(self):
        """Symlink libpython*.a to save space"""
        libs = []
        for dirpath, dirnames, filenames in os.walk(self.install_folder / "lib"):
            for name in filenames:
                if name.startswith("libpython"):
                    libs.append(os.path.join(dirpath, name))

        if len(libs) == 2:
            shorter, longer = sorted(libs, key=lambda x: len(x))
            shorter_size = runez.to_path(shorter).stat().st_size
            longer_size = runez.to_path(longer).stat().st_size
            if shorter_size == longer_size:  # Double-check that they are the same size (they should be identical)
                runez.symlink(longer, shorter)

    @runez.cached_property
    def cleanable_basenames(self):
        """Folders that are not useful in deliverable (test suites etc)"""
        r = {
            "__phello__.foo.py",
            "__pycache__",  # Clear it because lots of unneeded stuff is in there initially, -mcompileall regenerates it
            "_bundled",
            "idle_test",
            "test",
            "tests",
        }
        if not self.setup.static:
            # Don't keep static compilation file unless --static
            r.add(f"libpython{self.version.major}.{self.version.minor}.a")

        return r

    @runez.cached_property
    def cleanable_prefixes(self):
        """Files/folders that are not useful in deliverable, but name varies, can be identified by their prefix"""
        r = set()
        if not self.setup.static:
            r.add("config-%s.%s-" % (self.version.major, self.version.minor))

        return r

    def should_clean(self, basename):
        return basename in self.cleanable_basenames or any(basename.startswith(x) for x in self.cleanable_prefixes)

    def cleanup_distribution(self):
        cleaned = []
        for dirpath, dirnames, filenames in os.walk(self.install_folder):
            removed = []
            for name in dirnames:
                if self.should_clean(name):
                    # Remove unnecessary file, to save on space
                    full_path = os.path.join(dirpath, name)
                    removed.append(name)
                    cleaned.append(name)
                    runez.delete(full_path, logger=None)

            for name in removed:
                dirnames.remove(name)

            for name in filenames:
                if self.should_clean(name):
                    full_path = os.path.join(dirpath, name)
                    cleaned.append(name)
                    runez.delete(full_path, logger=None)

        if cleaned:
            names = runez.joined(sorted(set(cleaned)))
            LOG.info("Cleaned %s: %s" % (runez.plural(cleaned, "build artifact"), runez.short(names)))

    def correct_symlinks(self):
        with runez.CurrentFolder(self.bin_folder):
            all_files = {}
            files = {}
            symlinks = {}
            cleanable = ("2to3", "easy_install", "idle3", "pip", "pydoc", "wheel")
            for f in runez.ls_dir(self.bin_folder):
                if f.name.startswith(cleanable):
                    runez.delete(f)  # Get rid of old junk, can be pip installed if needed
                    continue

                all_files[f.name] = f
                if f.is_symlink():
                    symlinks[f.name] = f

                else:
                    files[f.name] = f

            if "python" not in all_files:
                runez.symlink(self.main_python, "python")

            for f in files.values():
                if f.name != self.main_python:
                    self._auto_correct_shebang(f)

    def _auto_correct_shebang(self, path):
        lines = []
        with open(path) as fh:
            for line in fh:
                if lines:
                    lines.append(line)

                elif not line.startswith("#!") or "bin/python" not in line:
                    return

                else:
                    lines.append("#!/bin/sh\n")
                    lines.append('"exec" "$(dirname $0)/%s" "$0" "$@"\n' % self.main_python)

        LOG.info("Auto-corrected shebang for %s" % runez.short(path))
        with open(path, "wt") as fh:
            for line in lines:
                fh.write(line)
