"""T0: the schema contract. No network, no testbed, runs in about a second.

These tests are the evidence for the project's central safety claim -- that an
unsafe BIND configuration is unrepresentable rather than merely discouraged. The
injection cases in particular must keep passing; if one of them ever starts
rendering instead of raising, the closed-world property is gone.

Run with either:
    python3 -m pytest config_tuner/tests/test_schema.py
    python3 config_tuner/tests/test_schema.py        # stdlib fallback runner
"""

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPTIMIZATION = os.path.join(REPO, "ns_software", "bind", "optimization")
sys.path.insert(0, OPTIMIZATION)

import render_config as rc  # noqa: E402

SCHEMA = rc.load_schema()


def _raises(fn, *args, **kwargs):
    """Return the SchemaError a call produces, or fail loudly if it produces none."""
    try:
        result = fn(*args, **kwargs)
    except rc.SchemaError as e:
        return e
    raise AssertionError(f"expected SchemaError, got {result!r}")


# ------------------------------------------------------------------ structure


def test_schema_loads_and_is_closed():
    assert SCHEMA["version"] == 1
    names = list(rc.params_by_name(SCHEMA))
    assert "minimal_responses" in names
    assert "named_threads" in names
    # The closed-world property depends on there being no free-text knob.
    for param in SCHEMA["params"]:
        assert param["type"] in ("int", "enum"), param["name"]


def test_every_param_has_a_default():
    # canonical() fills defaults, so a missing one would make an empty candidate
    # unrepresentable and break baseline hashing.
    for param in SCHEMA["params"]:
        assert param.get("default") is not None, param["name"]


def test_named_u_flag_is_absent():
    # -U is a no-op on BIND >= 9.18; tuning it would burn evaluations on a knob
    # that cannot move the metric.
    assert "named_udp_listeners" not in rc.params_by_name(SCHEMA)


# ---------------------------------------------------------------- canonicality


def test_empty_candidate_equals_all_defaults():
    empty = rc.canonical(SCHEMA, {})
    explicit = rc.canonical(SCHEMA, dict(empty))
    assert empty == explicit
    assert rc.candidate_id(SCHEMA, {}) == rc.candidate_id(SCHEMA, dict(empty))


def test_candidate_id_is_key_order_independent():
    a = {"querylog": "no", "tcp_clients": 300}
    b = {"tcp_clients": 300, "querylog": "no"}
    assert rc.candidate_id(SCHEMA, a) == rc.candidate_id(SCHEMA, b)


def test_candidate_id_changes_with_value():
    base = rc.candidate_id(SCHEMA, {})
    moved = rc.candidate_id(SCHEMA, {"tcp_clients": 300})
    assert base != moved
    assert len(base) == 16


def test_canonical_is_in_schema_declaration_order():
    canon = rc.canonical(SCHEMA, {})
    assert list(canon) == [p["name"] for p in SCHEMA["params"]]


# -------------------------------------------------------------------- coercion


def test_unknown_key_rejected():
    err = _raises(rc.canonical, SCHEMA, {"definitely_not_a_knob": 1})
    assert "unknown parameter" in str(err)


def test_int_out_of_range_rejected():
    assert "outside" in str(_raises(rc.canonical, SCHEMA, {"tcp_clients": 999999}))
    assert "outside" in str(_raises(rc.canonical, SCHEMA, {"tcp_clients": -1}))


def test_off_step_int_rejected():
    # max_udp_size has step 16 from a min of 512.
    assert "multiple" in str(_raises(rc.canonical, SCHEMA, {"max_udp_size": 1000}))
    rc.canonical(SCHEMA, {"max_udp_size": 1232})  # 512 + 45*16, accepted


def test_non_member_enum_rejected():
    err = _raises(rc.canonical, SCHEMA, {"minimal_responses": "maybe"})
    assert "not one of" in str(err)


def test_bool_rejected_for_both_types():
    _raises(rc.canonical, SCHEMA, {"querylog": True})
    _raises(rc.canonical, SCHEMA, {"tcp_clients": True})


def test_non_integer_rejected():
    _raises(rc.canonical, SCHEMA, {"tcp_clients": 1.5})
    _raises(rc.canonical, SCHEMA, {"tcp_clients": "300; querylog yes"})
    _raises(rc.canonical, SCHEMA, {"tcp_clients": None})


def test_omit_when_sentinel_allowed_outside_range():
    # 0 means "leave the OS default alone" even though min is 65536.
    canon = rc.canonical(SCHEMA, {"udp_receive_buffer": 0})
    assert canon["udp_receive_buffer"] == 0
    assert "udp-receive-buffer" not in rc.render(SCHEMA, {"udp_receive_buffer": 0})["options"]


def test_fact_cap_clamps_max():
    # named_threads declares max 64, but a 40-core host must cap at 40.
    rc.canonical(SCHEMA, {"named_threads": 48})  # fine without facts
    err = _raises(rc.canonical, SCHEMA, {"named_threads": 48}, facts={"nproc": 40})
    assert "clamped to 40" in str(err)
    rc.canonical(SCHEMA, {"named_threads": 32}, facts={"nproc": 40})


def test_constraints_enforced():
    err = _raises(
        rc.canonical, SCHEMA,
        {"udp_receive_buffer": 1048576, "net_core_rmem_max": 212992},
    )
    assert "buffers_need_headroom" in str(err)


# ------------------------------------------------------------------- injection
# The heart of the safety claim. Each of these is a plausible attempt to break
# out of a directive; every one must raise before anything is rendered.


def test_injection_via_enum_is_rejected():
    for hostile in [
        'no; }; options { recursion yes; };',
        'no";\n    allow-transfer { any; };\n    "',
        "no\n};\noptions {\n  recursion yes;",
        "no; allow-recursion { any; }",
    ]:
        err = _raises(rc.canonical, SCHEMA, {"minimal_responses": hostile})
        assert "not one of" in str(err), hostile


def test_injection_via_int_is_rejected():
    for hostile in ["8; querylog yes", "8}\noptions{recursion yes", "0x10", "8 8"]:
        _raises(rc.canonical, SCHEMA, {"named_threads": hostile})


def test_emitter_backstop_catches_unsafe_tokens():
    # Even if a future edit let a bad value past coercion, the emitter refuses.
    assert "refusing to emit" in str(_raises(rc._token, "yes; recursion yes"))
    assert "refusing to emit" in str(_raises(rc._token, 'a"b'))
    assert "refusing to emit" in str(_raises(rc._token, "a b"))
    assert rc._token("no-auth") == "no-auth"
    assert rc._token(4096) == "4096"


# --------------------------------------------------------------------- render


def test_invariants_always_present():
    for candidate in [{}, {"minimal_responses": "yes"}, {"querylog": "yes"}]:
        options = rc.render(SCHEMA, candidate)["options"]
        assert "recursion no;" in options
        assert "listen-on { 10.10.1.2; 127.0.0.1; };" in options
        assert "allow-query { any; };" in options
        assert "allow-recursion { none; };" in options
        # No candidate may turn the server into a resolver.
        assert "recursion yes" not in options


def test_render_is_deterministic():
    a = rc.render(SCHEMA, {"tcp_clients": 300})
    b = rc.render(SCHEMA, {"tcp_clients": 300})
    assert a["options"] == b["options"]
    assert a["candidate_id"] == b["candidate_id"]


def test_startup_omits_flag_at_sentinel():
    assert "-n" not in rc.render(SCHEMA, {"named_threads": 0})["startup"]
    assert "-n 16" in rc.render(SCHEMA, {"named_threads": 16})["startup"]
    assert "-u bind" in rc.render(SCHEMA, {})["startup"]


def test_sysctl_lines_render():
    text = rc.render(SCHEMA, {"netdev_max_backlog": 50000})["sysctl"]
    assert "net.core.netdev_max_backlog = 50000" in text


def test_nic_requires_a_pinned_interface():
    # A candidate that changes the NIC without a host-supplied interface must
    # fail closed rather than guess which interface to touch.
    err = _raises(rc.render, SCHEMA, {"nic_rx_ring": 4096})
    assert "pinned interface" in str(err)

    text = rc.render(SCHEMA, {"nic_rx_ring": 4096}, iface="enp94s0f1")["nic"]
    assert "TUNER_IFACE=enp94s0f1" in text
    assert "ETHTOOL_RING_RX=4096" in text


def test_candidate_cannot_name_an_interface():
    # There is no interface parameter, so the optimizers cannot express one.
    assert not [p for p in SCHEMA["params"] if "iface" in p["name"]]
    _raises(rc.canonical, SCHEMA, {"nic_iface": "eth0"})


def test_no_nic_ops_when_untouched():
    result = rc.render(SCHEMA, {}, iface="enp94s0f1")
    assert result["nic_ops"] == {}


# ------------------------------------------------------------------------ CLI


def test_cli_hash_matches_library():
    candidate = {"tcp_clients": 300}
    proc = subprocess.run(
        [sys.executable, os.path.join(OPTIMIZATION, "render_config.py"), "--hash"],
        input=json.dumps(candidate), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == rc.candidate_id(SCHEMA, candidate)


def test_cli_rejects_bad_candidate_with_exit_2():
    proc = subprocess.run(
        [sys.executable, os.path.join(OPTIMIZATION, "render_config.py"), "--hash"],
        input=json.dumps({"minimal_responses": "no; }; options { recursion yes; };"}),
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "invalid candidate" in proc.stderr


def test_cli_writes_all_artifacts():
    with tempfile.TemporaryDirectory() as out:
        proc = subprocess.run(
            [sys.executable, os.path.join(OPTIMIZATION, "render_config.py"),
             "--out-dir", out, "--iface", "enp94s0f1"],
            input=json.dumps({"named_threads": 16}), capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        for filename in ["named.conf.options", "default-named",
                         "99-dns-tuner.conf", "nic.env", "manifest.json"]:
            assert os.path.exists(os.path.join(out, filename)), filename
        with open(os.path.join(out, "manifest.json")) as f:
            manifest = json.load(f)
        assert manifest["candidate_id"] == proc.stdout.strip()
        assert manifest["iface"] == "enp94s0f1"
        assert len(manifest["schema_sha"]) == 64


if __name__ == "__main__":
    # Minimal runner so these can be executed without pytest installed.
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok    {name}")
        except Exception as e:  # noqa: BLE001 - a test runner reports everything
            failures.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
