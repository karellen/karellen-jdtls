#!/usr/bin/env python3

import argparse
import os
import sys
import tarfile
import zipfile
from functools import partial
from pathlib import Path
from shutil import copy2, copytree, move, rmtree
from subprocess import check_call, check_output
from tempfile import TemporaryDirectory

log = partial(print, file=sys.stderr)

PLATFORMS = {
    "linux-gtk-x86_64": ("linux", "tar.gz"),
    "linux-gtk-aarch64": ("linux", "tar.gz"),
    "macosx-cocoa-x86_64": ("macos", "tar.gz"),
    "macosx-cocoa-aarch64": ("macos", "tar.gz"),
    "win32-x86_64": ("windows", "zip"),
}

LAUNCHER_PY = """\
import os
import site
import subprocess
import sys
import sysconfig


def _get_data_path():
    user_site = site.getusersitepackages()
    if __file__.startswith(user_site):
        scheme = 'nt_user' if os.name == 'nt' else 'posix_user'
        return sysconfig.get_path('data', scheme)
    return sysconfig.get_path('data')


def main():
    data = _get_data_path()
    if sys.platform == "darwin":
        jdtls = os.path.join(data, "lib", "karellen-jdtls-kotlin",
                             "Eclipse.app", "Contents", "MacOS", "jdtls")
    elif sys.platform == "win32":
        jdtls = os.path.join(data, "lib", "karellen-jdtls-kotlin", "jdtls.exe")
    else:
        jdtls = os.path.join(data, "lib", "karellen-jdtls-kotlin", "jdtls")

    if sys.platform == "win32":
        proc = subprocess.Popen([jdtls] + sys.argv[1:],
                                stdin=sys.stdin, stdout=sys.stdout,
                                stderr=sys.stderr, close_fds=False)
        sys.exit(proc.wait())
    else:
        os.execv(jdtls, [jdtls] + sys.argv[1:])


if __name__ == "__main__":
    main()
"""

SETUP_PY_TEMPLATE = """\
from os import walk
from os.path import abspath, join as jp

from setuptools import setup, find_namespace_packages
from wheel_axle.bdist_axle import BdistAxle

PYTHON_SRC_DIR = "python-src"


def get_data_files(src_dir):
    current_path = abspath(src_dir)
    for root, dirs, files in walk(current_path, followlinks=True):
        if not files:
            continue
        path_prefix = root[len(current_path) + 1:]
        if (path_prefix.endswith(".egg-info")
                or path_prefix.startswith(PYTHON_SRC_DIR)
                or path_prefix.startswith(".claude-tmp")
                or path_prefix.startswith("build")
                or path_prefix.startswith("dist")):
            continue
        # Skip build files at root level
        if not path_prefix:
            files = [f for f in files
                     if not f.endswith(("setup.py", "setup.cfg", "pyproject.toml"))]
            if not files:
                continue
        yield path_prefix, [jp(root, f) for f in files]


data_files = list(get_data_files("."))

setup(
    name=%(name)r,
    version=%(version)r,
    description=%(description)r,
    long_description=%(long_description)r,
    long_description_content_type='text/markdown',
    classifiers=[
        'Programming Language :: Python',
        'Programming Language :: Java',
        'Programming Language :: Kotlin',
        'Operating System :: POSIX :: Linux',
        'Operating System :: MacOS',
        'Operating System :: Microsoft :: Windows',
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Build Tools',
    ],
    keywords=['jdtls', 'kotlin', 'java', 'lsp', 'language-server'],
    author='Karellen, Inc.',
    author_email='supervisor@karellen.co',
    maintainer='Arcadiy Ivanov',
    maintainer_email='arcadiy@karellen.co',
    license='Apache-2.0',
    url='https://github.com/karellen/karellen-jdtls',
    project_urls={
        'Bug Tracker': 'https://github.com/karellen/karellen-jdtls/issues',
        'Documentation': 'https://github.com/karellen/karellen-jdtls',
        'Source Code': 'https://github.com/karellen/karellen-jdtls',
    },
    scripts=[],
    packages=find_namespace_packages(where=PYTHON_SRC_DIR),
    package_dir={'': PYTHON_SRC_DIR},
    package_data={'': ['*']},
    namespace_packages=[],
    py_modules=[],
    entry_points={
        'console_scripts': [
            'jdtls=karellen_jdtls.launcher:main',
        ],
    },
    data_files=data_files,
    install_requires=[],
    extras_require={},
    dependency_links=[],
    zip_safe=False,
    obsoletes=[],
    cmdclass={"bdist_wheel": BdistAxle},
)
"""

SETUP_CFG_TEMPLATE = """\
[bdist_wheel]
root_is_pure = false
python_tag = py3
abi_tag = none
%(plat_name_line)s"""

PYPROJECT_TOML = """\
[build-system]
requires = ["setuptools", "wheel", "wheel-axle>=0.0.12"]
build-backend = "setuptools.build_meta"
"""

parser = argparse.ArgumentParser(description="Package karellen-jdtls product into a Python wheel")
parser.add_argument("-p", "--product-dir", type=Path, required=True,
                    help="Path to materialized product directory "
                         "(e.g., co.karellen.jdtls.product/target/products/karellen-jdtls.product)")
parser.add_argument("-P", "--platform", type=str, required=True, choices=PLATFORMS.keys(),
                    help="Platform to package")
parser.add_argument("-v", "--version", type=str, required=True,
                    help="Wheel version (e.g., 1.0.0 or 1.0.0.dev202603221349)")
parser.add_argument("-o", "--output-dir", type=Path, default=Path("wheels"),
                    help="Output directory for wheels")
parser.add_argument("--plat-name", type=str, default=None,
                    help="Override wheel platform tag (e.g., macosx_11_0_arm64). "
                         "Falls back to AUDITWHEEL_PLAT env var if not set.")


PLATFORM_SUBDIRS = {
    "linux-gtk-x86_64": ("linux", "gtk", "x86_64"),
    "linux-gtk-aarch64": ("linux", "gtk", "aarch64"),
    "macosx-cocoa-x86_64": ("macosx", "cocoa", "x86_64"),
    "macosx-cocoa-aarch64": ("macosx", "cocoa", "aarch64"),
    "win32-x86_64": ("win32", "win32", "x86_64"),
}


def extract_product(product_dir: Path, platform: str) -> Path:
    """Return the platform-specific product root directory.
    Supports both Tycho materialized layout and flat extracted archives."""
    subdir = product_dir.joinpath(*PLATFORM_SUBDIRS[platform])
    if subdir.exists():
        return subdir
    # Flat layout — product_dir is the platform root directly
    return product_dir


def stage_product(platform_dir: Path, staging_dir: Path, platform: str):
    """Copy product contents into staging lib/karellen-jdtls-kotlin/."""
    lib_dir = staging_dir / "lib" / "karellen-jdtls-kotlin"
    os_type, _ = PLATFORMS[platform]

    if os_type == "macos":
        # Keep Eclipse.app as-is, exclude p2
        copytree(platform_dir / "Eclipse.app", lib_dir / "Eclipse.app",
                 symlinks=True,
                 ignore=lambda d, files: [f for f in files if f == "p2"])
    elif os_type == "windows":
        lib_dir.mkdir(parents=True, exist_ok=True)
        copy2(str(platform_dir / "jdtls.exe"), str(lib_dir / "jdtls.exe"))
        copy2(str(platform_dir / "jdtlsc.exe"), str(lib_dir / "jdtlsc.exe"))
        copy2(str(platform_dir / "jdtls.ini"), str(lib_dir / "jdtls.ini"))
        copytree(platform_dir / "plugins", lib_dir / "plugins", symlinks=True)
        copytree(platform_dir / "configuration", lib_dir / "configuration", symlinks=True)
    else:
        # Linux: flat layout
        lib_dir.mkdir(parents=True, exist_ok=True)
        copy2(str(platform_dir / "jdtls"), str(lib_dir / "jdtls"))
        copy2(str(platform_dir / "jdtls.ini"), str(lib_dir / "jdtls.ini"))
        copytree(platform_dir / "plugins", lib_dir / "plugins", symlinks=True)
        copytree(platform_dir / "configuration", lib_dir / "configuration", symlinks=True)


def create_python_package(staging_dir: Path):
    """Create the karellen_jdtls Python package with launcher."""
    pkg_dir = staging_dir / "python-src" / "karellen_jdtls"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "launcher.py").write_text(LAUNCHER_PY, encoding="utf-8")


def create_build_files(staging_dir: Path, version: str, plat_name: str | None = None):
    """Generate pyproject.toml, setup.py, and setup.cfg in staging dir."""
    (staging_dir / "pyproject.toml").write_text(PYPROJECT_TOML, encoding="utf-8")

    setup_py = SETUP_PY_TEMPLATE % dict(
        name="karellen-jdtls",
        version=version,
        description="Karellen JDTLS — Eclipse JDT Language Server with Kotlin support",
        long_description="Self-contained Eclipse JDT Language Server distribution "
                         "with cross-language Java/Kotlin support via the "
                         "karellen-jdtls-kotlin search participant plugin.",
    )
    (staging_dir / "setup.py").write_text(setup_py, encoding="utf-8")
    plat = plat_name or os.environ.get("AUDITWHEEL_PLAT", "")
    plat_name_line = f"plat_name = {plat}" if plat else ""
    setup_cfg = SETUP_CFG_TEMPLATE % dict(plat_name_line=plat_name_line)
    (staging_dir / "setup.cfg").write_text(setup_cfg, encoding="utf-8")


def build_wheel(staging_dir: Path, output_dir: Path):
    """Run python -m build --wheel --no-isolation in the staging directory."""
    check_call([sys.executable, "-m", "build", "--wheel", "--no-isolation"],
               cwd=staging_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    dist_dir = staging_dir / "dist"
    for whl in dist_dir.glob("*.whl"):
        dest = output_dir / whl.name
        log(f"Moving {whl.name} -> {dest}")
        move(str(whl), str(dest))


def main():
    args = parser.parse_args()

    product_dir = args.product_dir
    platform = args.platform
    version = args.version
    output_dir = args.output_dir

    platform_dir = extract_product(product_dir, platform)
    if not platform_dir.exists():
        log(f"ERROR: Product directory does not exist: {platform_dir}")
        sys.exit(1)

    log(f"Packaging {platform} from {platform_dir}")
    log(f"Version: {version}")

    with TemporaryDirectory() as tmp:
        staging_dir = Path(tmp)

        log("Staging product contents...")
        stage_product(platform_dir, staging_dir, platform)

        log("Creating Python package...")
        create_python_package(staging_dir)

        log("Creating build files...")
        create_build_files(staging_dir, version, plat_name=args.plat_name)

        log("Building wheel...")
        build_wheel(staging_dir, output_dir)

    log("Done!")


if __name__ == "__main__":
    main()
