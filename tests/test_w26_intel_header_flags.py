"""Actual fixed stage environment/header consumption, without compiler execution."""
import os
from pathlib import Path
import shlex
import pytest
from tools import w26_intel_derived as d


@pytest.mark.skipif(not (d.PYTHON.parents[1]/'include/python3.12/Python.h').is_file(), reason='SKIP_ENV/NOT_RUN: exact accepted product CPython headers absent')
def test_product_header_is_explicit_in_actual_cc_rs_consumed_flags(tmp_path):
    env=d.environment(tmp_path)
    header=d.PYTHON.parents[1]/'include/python3.12'
    assert (header/'Python.h').is_file()
    assert (header/'pyconfig.h').is_file()
    for key in ('CFLAGS','CFLAGS_x86_64_apple_darwin'):
        flags=shlex.split(env[key])
        assert '-I'+str(header) in flags
        assert flags[flags.index('-arch')+1]=='x86_64'
        assert '-mmacosx-version-min=12.0' in flags
    assert env['PYO3_CROSS_LIB_DIR']==str(d.PYTHON.parents[1]/'lib')
    assert env['PYO3_PYTHON']==str(d.PYTHON) and env['PYO3_CROSS_PYTHON_VERSION']=='3.12'
    assert env['CPPFLAGS']=='-I'+str(header)


@pytest.mark.parametrize('variable,value',[('CFLAGS','-I/other/python3.13'),('CFLAGS_x86_64_apple_darwin','-arch arm64'),('CPPFLAGS','-mmacosx-version-min=27.0')])
def test_caller_environment_cannot_swap_headers_arch_or_floor(tmp_path,monkeypatch,variable,value):
    monkeypatch.setenv(variable,value)
    env=d.environment(tmp_path)
    assert '-I'+str(d.PYTHON.parents[1]/'include/python3.12') in env['CFLAGS']
    assert env['CFLAGS_x86_64_apple_darwin']==env['CFLAGS']
    assert value not in env['CFLAGS']


def test_eight_stage_environment_exactly_uses_fixed_header_flags(tmp_path):
    env=d.environment(tmp_path);rows=d.stages(tmp_path)
    assert len(rows)==8 and all(row['environment']==env for row in rows)
    assert rows[-1]['argv'][rows[-1]['argv'].index('--features')+1]=='pyo3/abi3-py312'
    assert rows[-1]['argv'][rows[-1]['argv'].index('--target')+1]=='x86_64-apple-darwin'
    assert rows[-1]['argv'][rows[-1]['argv'].index('--interpreter')+1]==str(d.PYTHON)
    assert env['CARGO_BUILD_JOBS']=='2' and env['CARGO_NET_OFFLINE']=='true'
