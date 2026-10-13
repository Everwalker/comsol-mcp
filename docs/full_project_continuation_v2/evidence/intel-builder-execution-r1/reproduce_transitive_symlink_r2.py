"""Reproduce R2 extraction only inside an empty task-local jail."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import prepare_offline_builder as production

with tempfile.TemporaryDirectory(prefix="comsol-tar-link-negative-") as temporary:
    jail = Path(temporary).resolve() / "jail"
    jail.mkdir()
    dest = jail / "extraction"
    dest.mkdir()
    archive = jail / "fixture.tar"
    with tarfile.open(archive, "w") as t:
        for name, target in (("top/deep/a/link", "../.."),
                             ("top/deep/a/linkB", "link/../..")):
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            t.addfile(member)
    af = os.open(archive, os.O_RDONLY)
    df = os.open(dest, os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = production.extract(af, df)
        link = dest / "top/deep/a/linkB"
        resolved = link.resolve()
        # Only resolve metadata inside this newly created empty jail. No external
        # data is opened, read, written or used as a target.
        assert resolved == jail
        escaped = not resolved.is_relative_to(dest)
        print(json.dumps({"production_function": "prepare_offline_builder.extract", "result": result,
                          "escaping_chain_accepted": escaped, "resolved_target_is_empty_owned_jail": resolved == jail,
                          "external_real_data_read_or_written": False,
                          "verdict": "CHANGES_REQUIRED" if escaped else "PASS"}))
    finally:
        os.close(af)
        os.close(df)
