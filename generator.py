import argparse
import re
from collections import defaultdict
from os import chmod, makedirs, mkdir, symlink
from pathlib import Path
from shutil import copy2, rmtree
from subprocess import CalledProcessError, check_output

import yaml
from distgen.multispec import Multispec


def run_distgen(src, dest, multispec_path, distro_config, version):
    cmd = [
        "dg",
        "--multispec",
        multispec_path,
        "--template",
        src,
        "--distro",
        distro_config,
        "--multispec-selector",
        f"version={version}",
        "--output",
        str(dest),
    ]

    try:
        check_output(cmd)
    except CalledProcessError as e:
        print("[ERROR] distgen failed:", e)


def get_version_distro_mapping(multispec_file):
    """Get all combinations from multispec file like:

    [{"distro": "rhel-8-x86_64.yaml", "version": "3.9"},
     {"distro": "rhel-8-x86_64.yaml", "version": "3.8"},
     {"distro": "centos-7-x86_64.yaml", "version": "3.8"},
     {"distro": "centos-stream-9-x86_64.yaml", "version": "3.9"}]

    and transfer them to:

    {"3.8": ["rhel-8-x86_64.yaml", "centos-7-x86_64.yaml"],
     "3.9": ["rhel-8-x86_64.yaml", "centos-stream-9-x86_64.yaml"]}
    """
    multispec_yaml = yaml.load(multispec_file.read(), Loader=yaml.SafeLoader)
    multispec = Multispec(data=multispec_yaml)
    mapping = defaultdict(list)
    for combination in multispec.get_all_combinations():
        mapping[combination["version"]].append(combination["distro"])
    return mapping


def filename_to_distro_config(filename, version, mapping):
    """Find distgen distro config from a filename.

    This is usually needed only for dockerfiles.
    - Dockerfile.rhelXX → rhel-XX-x86_64.yaml
    - Dockerfile.cXXs → centos-stream-XX-x86_64.yaml
    - Dockerfile.fedora → the newest fedora-XX-x86_64.yaml
    - Dockerfile → centos-7-x86_64.yaml

    If not found, None is returned indicating that the
    combination of distro and version is not included
    in distgen configuration (multispec).
    """
    if m := re.match(r".*\.rhel(\d+)$", filename):
        config = f"rhel-{m.group(1)}-x86_64.yaml"
    elif m := re.match(r".*\.c(\d+)s$", filename):
        config = f"centos-stream-{m.group(1)}-x86_64.yaml"
    elif filename.endswith(".fedora"):
        sorted_configs = sorted(
            c for c in mapping[version] if c.startswith("fedora")
        )
        if sorted_configs:
            config = sorted_configs[-1]
        else:
            config = None
    else:
        config = "centos-7-x86_64.yaml"

    if config in mapping[version]:
        return config
    return None


def parse_args():
    arg_parser = argparse.ArgumentParser(
        description="Helper script for distgen in S2I container images"
    )
    arg_parser.add_argument(
        "-v",
        "--version",
        dest="version",
        help="Version of image to generate sources for",
        required=True,
    )
    arg_parser.add_argument(
        "-m",
        "--manifest",
        dest="manifest",
        help="Path to manifest YAML file",
        type=argparse.FileType("r"),
        required=True,
    )
    arg_parser.add_argument(
        "-s",
        "--multispec",
        dest="multispec",
        help="Path to multispec YAML file",
        type=argparse.FileType("r"),
        required=True,
    )

    return arg_parser.parse_args()


def main():
    args = parse_args()
    manifest = yaml.load(args.manifest, Loader=yaml.SafeLoader)
    version_distro_map = get_version_distro_mapping(args.multispec)

    rmtree(args.version, ignore_errors=True)
    mkdir(args.version)

    for section in manifest:
        for spec in manifest[section]:
            # Prepend {version}/ to all destination paths
            spec["dest"] = Path(args.version) / spec["dest"]

            if not spec["dest"].parent.exists():
                makedirs(spec["dest"].parent)

            if section == "COPY_RULES":
                print(f"CP\t{spec['src']} → {spec['dest']}")
                copy2(spec["src"], spec["dest"])

            elif section == "SYMLINK_RULES":
                print(f"LN\t{spec['src']} → {spec['dest']}")
                symlink(spec["src"], spec["dest"])

            elif section == "DISTGEN_RULES":
                print(f"DG\t{spec['src']} → {spec['dest']}")
                # For common files like README.md or test/run
                # we need to run distgen only once and it does not
                # matter which distro config we use.
                distro_config = version_distro_map[args.version][0]
                run_distgen(
                    spec["src"],
                    spec["dest"],
                    args.multispec.name,
                    distro_config,
                    args.version,
                )

            elif section == "DISTGEN_MULTI_RULES":
                distro_config = filename_to_distro_config(
                    spec["dest"].name, args.version, version_distro_map
                )
                if distro_config:
                    print(f"DGM\t{spec['src']} → {spec['dest']}")
                    run_distgen(
                        spec["src"],
                        spec["dest"],
                        args.multispec.name,
                        distro_config,
                        args.version,
                    )

            else:
                print("[WARNING] Unexpected section:", section)

            if "mode" in spec:
                chmod(spec["dest"], int(spec["mode"], base=8))
                pass


if __name__ == "__main__":
    main()