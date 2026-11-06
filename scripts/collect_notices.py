"""Collect the installed build environment's third-party redistribution notices."""

import concurrent.futures
import hashlib
import io
import json
import shutil
import sys
import sysconfig
import tarfile
import time
import tomllib
import urllib.request
from importlib.metadata import distributions, version
from pathlib import Path

GRAMMARS = ("clojure", "go", "java", "javascript", "python", "rust", "tsx", "typescript")
GRAMMAR_RECORD_SHA256 = "b836efa79163af6967db6ad761bb753e642fbb6c718df6fd4410d0aba29ebf90"
PBS_REVISION = "de549958c79576345140088bd5482d0b0f8a2614"
PBS_SHA256 = "979e385ae47840c46de1ae9ff07dbedfbc650b54055dde6361a93334ccefcc8f"
UPSTREAM_LICENSE_FILES = {
    "jni": ("LICENSE-MIT", "LICENSE-APACHE"),
    "jni-macros": ("LICENSE-MIT", "LICENSE-APACHE"),
    "jni-sys-macros": ("LICENSE-MIT", "LICENSE-APACHE"),
    "rustls-platform-verifier-android": ("LICENSE-MIT", "LICENSE-APACHE"),
    "valuable": ("LICENSE",),
    "winapi-i686-pc-windows-gnu": ("LICENSE-MIT", "LICENSE-APACHE"),
    "winapi-x86_64-pc-windows-gnu": ("LICENSE-MIT", "LICENSE-APACHE"),
}
WINAPI_REVISION = "796a8e6c2971dc2ff1bcff166e6671284f9b5b6b"
LICENSE_PREFIXES = ("LICENSE", "LICENCE", "COPYING", "COPYRIGHT", "NOTICE", "UNLICENSE")


def collect_crate(component: dict, licenses: Path) -> tuple[str, int]:
    name, version = component["name"], component["version"]
    if any(Path(value).name != value or value in (".", "..") for value in (name, version)):
        raise RuntimeError("Unsafe Rust crate name or version.")
    digest = next(item["content"] for item in component["hashes"] if item["alg"] == "SHA-256")
    url = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError(f"Rust crate digest mismatch: {name} {version}")
    target = licenses / "rust" / f"{name}-{version}"
    copied = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if not member.isfile():
                continue
            is_license = path.name.upper().startswith(LICENSE_PREFIXES)
            is_r_efi_authors = name == "r-efi" and path.name == "AUTHORS"
            if not (is_license or is_r_efi_authors):
                continue
            relative = Path(*path.parts[1:])
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"Unsafe notice path in Rust crate: {member.name}")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source:
                destination.write_bytes(source.read())
            copied += 1
        if not copied and name in UPSTREAM_LICENSE_FILES:
            manifest = tomllib.loads(archive.extractfile(f"{name}-{version}/Cargo.toml").read().decode())
            repository = manifest["package"]["repository"].removesuffix(".git")
            if not repository.startswith("https://github.com/"):
                raise RuntimeError(f"Unsupported Rust license source: {repository}")
            if name.startswith("winapi-"):
                revision = WINAPI_REVISION
            else:
                vcs = json.loads(archive.extractfile(f"{name}-{version}/.cargo_vcs_info.json").read())
                revision = vcs["git"]["sha1"]
            upstream = repository.replace("https://github.com/", "https://raw.githubusercontent.com/")
            target.mkdir(parents=True, exist_ok=True)
            sources = []
            for filename in UPSTREAM_LICENSE_FILES[name]:
                license_url = f"{upstream}/{revision}/{filename}"
                with urllib.request.urlopen(license_url, timeout=60) as response:
                    (target / filename).write_bytes(response.read())
                sources.append(license_url)
                copied += 1
            (target / "UPSTREAM_SOURCES.txt").write_text("\n".join(sources) + "\n", encoding="utf-8")
    target.mkdir(parents=True, exist_ok=True)
    (target / "SOURCE.txt").write_text(f"{url}\nSHA-256: {digest}\n", encoding="utf-8")
    return f"{name} {version}", copied


def collect_rust_notices(sbom: Path, licenses: Path) -> int:
    components = json.loads(sbom.read_text(encoding="utf-8"))["components"]
    registry = [component for component in components if component.get("hashes")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda component: collect_crate(component, licenses), registry))
    missing = [name for name, count in results if not count]
    if missing:
        raise RuntimeError("Rust crates have no license texts: " + ", ".join(missing))
    return len(results)


def collect(output: Path) -> None:
    licenses = output / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    inventory = ["# Third-party notices", "", "Included package license texts and provenance follow.", ""]
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"Python license text missing: {python_license}")
    shutil.copyfile(python_license, licenses / "PYTHON_LICENSE.txt")
    inventory.extend([f"Python {sys.version.split()[0]} — licenses/PYTHON_LICENSE.txt", ""])

    pbs_url = f"https://codeload.github.com/astral-sh/python-build-standalone/tar.gz/{PBS_REVISION}"
    with urllib.request.urlopen(pbs_url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != PBS_SHA256:
        raise RuntimeError("Python native license source digest mismatch.")
    source_archive = io.BytesIO(data)
    runtime_licenses = licenses / "python-build-standalone"
    runtime_licenses.mkdir(exist_ok=True)
    with tarfile.open(fileobj=source_archive, mode="r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if len(path.parts) != 2 or not path.name.startswith("LICENSE") or not member.isfile():
                continue
            with archive.extractfile(member) as source:
                (runtime_licenses / path.name).write_bytes(source.read())
    inventory.extend([
        "## Python native library license texts", "",
        f"Source collection: {pbs_url}",
        "Available native-library license texts are in licenses/python-build-standalone/.", "",
    ])

    for package in sorted(distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = package.metadata["Name"]
        if name.lower() == "ub-cli":
            continue
        included = []
        for item in package.files or []:
            path = Path(str(item))
            is_notice = path.name.upper().startswith(LICENSE_PREFIXES)
            is_sbom = "sboms" in path.parts and path.suffix == ".json"
            if not (is_notice or is_sbom):
                continue
            source = Path(package.locate_file(item))
            if not source.is_file():
                continue
            target = licenses / f"{name}-{package.version}" / path
            if not target.resolve().is_relative_to(licenses.resolve()):
                raise RuntimeError(f"Unsafe installed notice path: {item}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            included.append(target.relative_to(output).as_posix())
            if name.lower() == "tree-sitter-language-pack" and is_sbom:
                crate_count = collect_rust_notices(source, licenses)
                inventory.extend([
                    "## Parser native dependencies", "",
                    f"License texts from {crate_count} SHA-256-verified registry crate archives are in licenses/rust/.",
                    "The workspace package uses its installed LICENSE file below.", "",
                ])
        if not included:
            raise RuntimeError(f"No license text installed for {name} {package.version}")
        inventory.append(f"## {name} {package.version}")
        inventory.extend(f"- {item}" for item in included)
        inventory.append("")

    grammar_version = version("tree-sitter-language-pack")
    if grammar_version != "1.22.1":
        raise RuntimeError("Update grammar notice digest when changing tree-sitter-language-pack.")
    url = (
        "https://raw.githubusercontent.com/xberg-io/tree-sitter-language-pack/"
        f"v{grammar_version}/sources/grammar_notices.json"
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != GRAMMAR_RECORD_SHA256:
        raise RuntimeError("Grammar provenance digest mismatch.")
    record = json.loads(data)
    selected = {"languages": {name: record["languages"][name] for name in GRAMMARS}}
    (licenses / "GRAMMAR_PROVENANCE.json").write_text(
        json.dumps(selected, indent=2) + "\n", encoding="utf-8"
    )
    inventory.extend(["## Parser grammars", "", f"Source record: {url}", ""])
    for name in GRAMMARS:
        grammar = record["languages"][name]
        inventory.append(f"- {name}: {grammar['repo']} at {grammar['rev']}")
        for notice in grammar["license_files"]:
            digest = notice["sha256"]
            text = record["licenses"][digest]
            if hashlib.sha256(text.encode()).hexdigest() != digest:
                raise RuntimeError(f"Grammar license digest mismatch: {name}")
            (licenses / f"grammar-{name}-{Path(notice['path']).name}").write_text(
                text, encoding="utf-8"
            )
    (output / "THIRD_PARTY_NOTICES.md").write_text("\n".join(inventory) + "\n", encoding="utf-8")


if __name__ == "__main__":
    collect(Path(sys.argv[1]))
